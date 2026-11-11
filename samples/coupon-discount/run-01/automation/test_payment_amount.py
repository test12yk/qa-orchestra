from qa_api import Expect, hold


def test_TC_037_coupon_and_points_total_48000(api, member, coupon, order):
    # 상품 52,000원 + 배송비 3,000원에 정액 5,000원 쿠폰과 포인트 2,000P를 쓰면 최종 결제 금액은 48,000원이다
    m = member(points=2000)
    c = coupon(m, "FIXED", amount=5000)
    oid = order(m, [(52000, "GENERAL")], shipping=3000)["orderId"]
    api.ok("POST", f"/orders/{oid}/coupon", {"couponId": c})
    r = api.post(f"/orders/{oid}/points", {"amount": 2000})
    (Expect()
     .status("포인트 입력", r, 200)
     .equal("상품 금액", r.body.get("goodsAmount"), 52000)
     .equal("배송비", r.body.get("shippingFee"), 3000)
     .equal("쿠폰 할인", r.body.get("couponDiscount"), 5000)
     .equal("포인트 사용", r.body.get("usedPoints"), 2000)
     .equal("최종 결제 금액", r.body.get("payAmount"), 48000)
     .done())


def test_TC_038_coupon_before_points(api, member, coupon, order):
    # 정률 10% 쿠폰과 포인트 10,000P를 함께 쓰면 쿠폰을 먼저 계산해 할인 6,000원, 최종 47,000원이 된다
    m = member(points=10000)
    c = coupon(m, "RATE", rate=10, maxDiscount=10000)
    oid = order(m, [(60000, "GENERAL")], shipping=3000)["orderId"]
    api.ok("POST", f"/orders/{oid}/points", {"amount": 10000})  # 포인트를 먼저 입력
    r = api.post(f"/orders/{oid}/coupon", {"couponId": c})
    if r.body.get("couponDiscount") == 6300:
        hold("쿠폰 할인 -6,300원(최종 46,700원) — Q9 답(할인 대상에 배송비 포함 여부) 필요")
    (Expect()
     .status("쿠폰 적용", r, 200)
     .equal("쿠폰 할인(포인트 차감 전 금액 기준)", r.body.get("couponDiscount"), 6000)
     .equal("포인트 사용", r.body.get("usedPoints"), 10000)
     .equal("최종 결제 금액", r.body.get("payAmount"), 47000)
     .done())


def test_TC_051_no_coupon_pay_goods_plus_shipping(api, member, order):
    # 쿠폰 없이 결제하면 최종 결제 금액이 상품 금액 + 배송비 그대로다
    m = member()
    o = order(m, [(25000, "GENERAL")], shipping=3000)
    pay = api.post(f"/orders/{o['orderId']}/pay")
    (Expect()
     .equal("쿠폰 할인", o["couponDiscount"], 0)
     .equal("최종 결제 금액", o["payAmount"], 28000)
     .status("결제 승인", pay, 200)
     .equal("결제 승인 금액", pay.body.get("approvedAmount"), 28000)
     .done())


def test_TC_052_points_only_payment(api, member, order):
    # 쿠폰 없이 포인트 3,000P만 쓰는 결제가 기존처럼 된다
    m = member(points=3000)
    oid = order(m, [(20000, "GENERAL")], shipping=3000)["orderId"]
    api.ok("POST", f"/orders/{oid}/points", {"amount": 3000})
    pay = api.post(f"/orders/{oid}/pay")
    (Expect()
     .status("결제", pay, 200)
     .equal("최종 결제 금액", pay.body.get("approvedAmount"), 20000)
     .equal("포인트 잔액", api.ok("GET", f"/members/{m}")["points"], 0, fmt=lambda v: f"{v:,}P")
     .done())


def test_TC_053_points_balance_after_coupon_and_points(api, member, coupon, order):
    # 쿠폰과 포인트를 함께 쓴 결제 후 포인트 잔액은 사용한 포인트만큼만 줄어든다
    m = member(points=10000)
    c = coupon(m, "FIXED", amount=5000)
    oid = order(m, [(52000, "GENERAL")], shipping=3000)["orderId"]
    api.ok("POST", f"/orders/{oid}/coupon", {"couponId": c})
    api.ok("POST", f"/orders/{oid}/points", {"amount": 2000})
    pay = api.post(f"/orders/{oid}/pay")
    (Expect()
     .status("결제", pay, 200)
     .equal("결제 금액", pay.body.get("approvedAmount"), 48000)
     .equal("포인트 잔액(쿠폰 할인액이 빠지지 않음)", api.ok("GET", f"/members/{m}")["points"], 8000, fmt=lambda v: f"{v:,}P")
     .done())


def test_TC_055_shipping_unchanged_by_coupon(api, member, coupon, order):
    # 쿠폰을 적용해도 배송비 3,000원은 바뀌지 않는다
    m = member()
    c = coupon(m, "RATE", rate=10, maxDiscount=10000)
    o = order(m, [(50000, "GENERAL")], shipping=3000)
    r = api.post(f"/orders/{o['orderId']}/coupon", {"couponId": c})
    (Expect()
     .equal("쿠폰 적용 전 배송비", o["shippingFee"], 3000)
     .status("쿠폰 적용", r, 200)
     .equal("쿠폰 적용 후 배송비", r.body.get("shippingFee"), 3000)
     .done())


def test_TC_056_pg_amount_equals_pay_amount(api, member, coupon, order, pg):
    # 쿠폰과 포인트를 쓴 주문의 PG 승인 금액은 최종 결제 금액 48,000원과 같다
    pg("approve")
    m = member(points=2000)
    c = coupon(m, "FIXED", amount=5000)
    oid = order(m, [(52000, "GENERAL")], shipping=3000)["orderId"]
    api.ok("POST", f"/orders/{oid}/coupon", {"couponId": c})
    api.ok("POST", f"/orders/{oid}/points", {"amount": 2000})
    pay = api.post(f"/orders/{oid}/pay")
    detail = api.get(f"/orders/{oid}").body
    (Expect()
     .status("결제", pay, 200)
     .equal("PG 승인 금액", detail.get("pgApprovedAmount"), 48000)
     .equal("주문 상세 결제 금액", detail.get("payAmount"), 48000)
     .done())
