import threading

from conftest import coupon_in, usable_text
from qa_api import Expect


def test_TC_040_used_after_payment_approval(api, member, coupon, order, pg):
    # 쿠폰을 적용한 주문의 결제 승인이 완료되면 쿠폰이 USED로 바뀐다
    pg("approve")
    m = member()
    c = coupon(m, "FIXED", amount=5000)
    oid = order(m, [(40000, "GENERAL")], shipping=3000)["orderId"]
    api.ok("POST", f"/orders/{oid}/coupon", {"couponId": c})
    before = api.ok("GET", f"/coupons/{c}")["status"]
    pay = api.post(f"/orders/{oid}/pay")
    after = api.ok("GET", f"/coupons/{c}")["status"]
    other = coupon_in(order(m, [(40000, "GENERAL")], shipping=3000), c)
    (Expect()
     .equal("결제 승인 전 쿠폰 상태", before, "AVAILABLE", fmt=str)
     .status("결제 승인", pay, 200)
     .equal("결제 금액", pay.body.get("approvedAmount"), 38000)
     .equal("승인 완료 후 쿠폰 상태", after, "USED", fmt=str)
     .true("다른 주문서 쿠폰 목록", not other["usable"], usable_text(other), "사용 가능으로 나오지 않음")
     .done())


def test_TC_041_leave_without_payment_keeps_available(api, member, coupon, order):
    # 쿠폰을 적용하고 결제하지 않고 주문서를 나가면 쿠폰은 AVAILABLE 그대로다
    m = member()
    c = coupon(m, "FIXED", amount=5000)
    oid = order(m, [(40000, "GENERAL")], shipping=3000)["orderId"]
    api.ok("POST", f"/orders/{oid}/coupon", {"couponId": c})
    status = api.ok("GET", f"/coupons/{c}")["status"]
    again = coupon_in(order(m, [(40000, "GENERAL")], shipping=3000), c)
    (Expect()
     .equal("쿠폰 상태", status, "AVAILABLE", fmt=str)
     .true("새 주문서 쿠폰 목록", again["usable"], usable_text(again), "사용 가능")
     .done())


def test_TC_042_pg_decline_keeps_available(api, member, coupon, order, pg):
    # 쿠폰 적용 주문의 PG 결제 승인이 실패하면 쿠폰은 AVAILABLE로 남는다
    m = member()
    c = coupon(m, "FIXED", amount=5000)
    oid = order(m, [(40000, "GENERAL")], shipping=3000)["orderId"]
    api.ok("POST", f"/orders/{oid}/coupon", {"couponId": c})
    pg("decline")
    pay = api.post(f"/orders/{oid}/pay")
    (Expect()
     .true("결제 응답", pay.status >= 400, pay.brief(), "실패")
     .not_equal("주문 상태", api.ok("GET", f"/orders/{oid}")["status"], "PAID", fmt=str)
     .equal("쿠폰 상태", api.ok("GET", f"/coupons/{c}")["status"], "AVAILABLE", fmt=str)
     .done())


def test_TC_043_concurrent_payments_only_one_discounted(api, member, coupon, order, pg):
    # 같은 쿠폰을 적용한 두 주문을 동시에 결제하면 한 주문만 쿠폰 할인으로 결제 완료된다
    pg("approve")
    m = member()
    c = coupon(m, "FIXED", amount=5000)
    oids = [order(m, [(40000, "GENERAL")], shipping=3000)["orderId"] for _ in range(2)]
    for oid in oids:
        api.ok("POST", f"/orders/{oid}/coupon", {"couponId": c})
    results, start = {}, threading.Barrier(2)

    def pay(oid):
        start.wait()
        results[oid] = api.post(f"/orders/{oid}/pay")

    threads = [threading.Thread(target=pay, args=(oid,)) for oid in oids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    discounted = [oid for oid, r in results.items() if r.status == 200 and r.body.get("approvedAmount") == 38000]
    st = api.ok("GET", f"/coupons/{c}").body
    (Expect()
     .equal("쿠폰 할인으로 결제 완료된 주문 수", len(discounted), 1, fmt=lambda v: f"{v}건")
     .equal("쿠폰 상태", st["status"], "USED", fmt=str)
     .true("쿠폰 사용 주문", st.get("usedOrderId") in discounted, str(st.get("usedOrderId")), "할인 결제된 주문 1건")
     .done())


def test_TC_044_coupon_used_elsewhere_blocks_payment(api, member, coupon, order, pg):
    # 주문 A에 적용해 둔 쿠폰이 주문 B에서 먼저 USED 되면 주문 A는 쿠폰 할인으로 결제할 수 없다
    pg("approve")
    m = member()
    c = coupon(m, "FIXED", amount=5000)
    a = order(m, [(40000, "GENERAL")], shipping=3000)["orderId"]
    b = order(m, [(40000, "GENERAL")], shipping=3000)["orderId"]
    api.ok("POST", f"/orders/{a}/coupon", {"couponId": c})
    api.ok("POST", f"/orders/{b}/coupon", {"couponId": c})
    pay_b = api.post(f"/orders/{b}/pay")
    status_b = api.ok("GET", f"/coupons/{c}")["status"]
    pay_a = api.post(f"/orders/{a}/pay")
    (Expect()
     .status("주문 B 결제", pay_b, 200)
     .equal("주문 B 결제 후 쿠폰 상태", status_b, "USED", fmt=str)
     .true("주문 A 결제", not (pay_a.status == 200 and pay_a.body.get("approvedAmount") == 38000),
           f"{pay_a.brief()}, 승인 {pay_a.body.get('approvedAmount', 0):,}원", "38,000원(쿠폰 할인)으로 결제되지 않음")
     .done())


def test_TC_061_unused_coupon_stays_available(api, member, coupon, paid_order):
    # 쿠폰 2장 중 1장을 써서 결제하면 쓰지 않은 쿠폰은 AVAILABLE 그대로다
    m = member()
    a = coupon(m, "FIXED", amount=5000)
    b = coupon(m, "RATE", rate=10, maxDiscount=10000)
    paid_order(m, [(40000, "GENERAL")], shipping=3000, coupon_id=a)
    (Expect()
     .equal("쿠폰 A 상태", api.ok("GET", f"/coupons/{a}")["status"], "USED", fmt=str)
     .equal("쿠폰 B 상태", api.ok("GET", f"/coupons/{b}")["status"], "AVAILABLE", fmt=str)
     .done())


def test_TC_062_expired_coupon_not_selected(api, member, coupon, order, clock):
    # 유효기간이 지난 쿠폰은 주문서에서 자동 선택되지 않고 적용할 수 없다
    clock("2026-11-10T12:00:00")
    m = member()
    c = coupon(m, "FIXED", amount=5000, expiresAt="2026-11-09T23:59:59")
    o = order(m, [(40000, "GENERAL")], shipping=3000)
    r = api.post(f"/orders/{o['orderId']}/coupon", {"couponId": c})
    after = api.get(f"/orders/{o['orderId']}").body
    (Expect()
     .equal("자동 선택 쿠폰", o["autoSelectedCouponId"], None, fmt=str)
     .true("만료 쿠폰 적용 응답", r.status >= 400, r.brief(), "거부")
     .equal("쿠폰 할인", after["couponDiscount"], 0)
     .equal("최종 결제 금액", after["payAmount"], 43000)
     .done())
