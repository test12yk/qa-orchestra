from conftest import coupon_in, usable_text
from qa_api import Expect, hold


def test_TC_045_full_cancel_restores_coupon(api, member, coupon, paid_order):
    # 유효기간이 남은 쿠폰을 쓴 주문을 전체 취소하면 쿠폰이 AVAILABLE로 복구된다
    m = member()
    c = coupon(m, "FIXED", amount=5000, expiresAt="2026-11-30T23:59:59")
    o = paid_order(m, [(40000, "GENERAL")], shipping=3000, coupon_id=c)
    r = api.post(f"/orders/{o['orderId']}/cancel", {})
    st = api.ok("GET", f"/coupons/{c}").body
    (Expect()
     .status("전체 취소", r, 200)
     .equal("주문 상태", r.body.get("status"), "CANCELLED", fmt=str)
     .equal("쿠폰 상태", st["status"], "AVAILABLE", fmt=str)
     .equal("쿠폰 유효기간", st["expiresAt"][:10], "2026-11-30", fmt=str)
     .done())


def test_TC_046_restored_coupon_reusable(api, member, coupon, order, paid_order):
    # 전체 취소로 복구된 쿠폰을 새 주문에 다시 쓰면 할인되고 다시 USED가 된다
    m = member()
    c = coupon(m, "FIXED", amount=5000, expiresAt="2026-11-30T23:59:59")
    first = paid_order(m, [(40000, "GENERAL")], shipping=3000, coupon_id=c)
    api.ok("POST", f"/orders/{first['orderId']}/cancel", {})  # 사전조건: TC-045로 복구된 쿠폰
    oid = order(m, [(40000, "GENERAL")], shipping=3000)["orderId"]
    r = api.post(f"/orders/{oid}/coupon", {"couponId": c})
    pay = api.post(f"/orders/{oid}/pay")
    (Expect()
     .status("복구된 쿠폰 적용", r, 200)
     .equal("쿠폰 할인", r.body.get("couponDiscount"), 5000)
     .equal("최종 결제 금액", r.body.get("payAmount"), 38000)
     .status("결제 승인", pay, 200)
     .equal("결제 승인 후 쿠폰 상태", api.ok("GET", f"/coupons/{c}")["status"], "USED", fmt=str)
     .done())


def _paid_a_b(member, coupon, paid_order):
    m = member()
    c = coupon(m, "FIXED", amount=5000)
    o = paid_order(m, [(30000, "GENERAL"), (20000, "GENERAL")], shipping=3000, coupon_id=c)
    return m, c, o


def test_TC_047_partial_cancel_keeps_used(api, member, coupon, order, paid_order):
    # 쿠폰을 쓴 주문을 부분 취소하면 쿠폰은 복구되지 않고 USED로 남는다
    m, c, o = _paid_a_b(member, coupon, paid_order)
    api.ok("POST", f"/orders/{o['orderId']}/cancel", {"itemIndexes": [1]})  # 상품 B
    listed = coupon_in(order(m, [(40000, "GENERAL")], shipping=3000), c)
    (Expect()
     .equal("결제 금액(사전조건)", o["payAmount"], 48000)
     .equal("쿠폰 상태", api.ok("GET", f"/coupons/{c}")["status"], "USED", fmt=str)
     .true("새 주문서 쿠폰 목록", listed is None or not listed["usable"], usable_text(listed), "사용 가능으로 보이지 않음")
     .done())


def test_TC_048_refund_total_not_over_paid(api, member, coupon, paid_order):
    # 쿠폰 사용 주문을 부분 취소 후 나머지까지 취소하면 환불 합계가 결제 금액 48,000원을 넘지 않는다
    m, c, o = _paid_a_b(member, coupon, paid_order)
    oid = o["orderId"]
    rb = api.ok("POST", f"/orders/{oid}/cancel", {"itemIndexes": [1]})["refundAmount"]  # 상품 B
    ra = api.ok("POST", f"/orders/{oid}/cancel", {"itemIndexes": [0]})["refundAmount"]  # 상품 A
    total = ra + rb
    st = api.ok("GET", f"/coupons/{c}")["status"]
    Expect().true("환불 금액 합계", total <= 48000, f"{total:,}원(B {rb:,}원 + A {ra:,}원)", "48,000원 이하").done()
    hold(f"건별 환불 B {rb:,}원 · A {ra:,}원(합계 {total:,}원), 쿠폰 상태 {st} — Q8 답(안분 규칙)·Q4 답(쿠폰 복구) 필요")


def _expiring_paid_order(member, coupon, paid_order, clock):
    clock("2026-11-10T12:00:00")
    m = member()
    c = coupon(m, "FIXED", amount=5000, expiresAt="2026-11-10T23:59:59")
    o = paid_order(m, [(40000, "GENERAL")], shipping=3000, coupon_id=c)
    return m, c, o


def test_TC_049_cancel_one_second_before_expiry_restores(api, member, coupon, paid_order, clock):
    # 유효기간 마지막 1초 전(23:59:58)에 전체 취소하면 쿠폰이 AVAILABLE로 복구된다
    m, c, o = _expiring_paid_order(member, coupon, paid_order, clock)
    clock("2026-11-10T23:59:58")
    api.ok("POST", f"/orders/{o['orderId']}/cancel", {})
    Expect().equal("쿠폰 상태", api.ok("GET", f"/coupons/{c}")["status"], "AVAILABLE", fmt=str).done()


def test_TC_050_cancel_after_expiry_not_restored(api, member, coupon, order, paid_order, clock):
    # 유효기간이 지난 뒤(00:00:00)에 전체 취소하면 쿠폰은 복구되지 않는다
    # TC 메모: API 문서에 만료 미복구가 없어 AVAILABLE로 복구되면 결함 후보로 보고 → hold 없이 Fail
    m, c, o = _expiring_paid_order(member, coupon, paid_order, clock)
    clock("2026-11-11T00:00:00")
    r = api.post(f"/orders/{o['orderId']}/cancel", {})
    st = api.ok("GET", f"/coupons/{c}")["status"]
    listed = coupon_in(order(m, [(40000, "GENERAL")], shipping=3000), c)
    (Expect()
     .not_equal("쿠폰 상태", st, "AVAILABLE", fmt=str)
     .true("새 주문서 쿠폰 목록", not listed["usable"], usable_text(listed), "사용 가능으로 보이지 않음")
     .status("전체 취소", r, 200)
     .equal("환불 금액", r.body.get("refundAmount"), 38000)
     .done())


def test_TC_058_no_coupon_full_cancel_refund(api, member, paid_order):
    # 쿠폰을 쓰지 않은 주문을 전체 취소하면 결제 금액 28,000원이 전액 환불된다
    o = paid_order(member(), [(25000, "GENERAL")], shipping=3000)
    r = api.post(f"/orders/{o['orderId']}/cancel", {})
    (Expect()
     .status("전체 취소", r, 200)
     .equal("주문 상태", r.body.get("status"), "CANCELLED", fmt=str)
     .equal("환불 금액", r.body.get("refundAmount"), 28000)
     .done())


def test_TC_060_coupon_order_refunds_paid_amount_only(api, member, coupon, paid_order):
    # 정액 5,000원 쿠폰을 쓴 주문을 전체 취소하면 실제 결제한 38,000원만 환불된다
    m = member()
    c = coupon(m, "FIXED", amount=5000)
    o = paid_order(m, [(40000, "GENERAL")], shipping=3000, coupon_id=c)
    r = api.post(f"/orders/{o['orderId']}/cancel", {})
    (Expect()
     .status("전체 취소", r, 200)
     .equal("환불 금액(43,000원 아님)", r.body.get("refundAmount"), 38000)
     .done())
