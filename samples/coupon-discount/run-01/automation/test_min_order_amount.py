from conftest import coupon_in, usable_text
from qa_api import Expect


def test_TC_001_free_shipping_30000_coupon_usable(api, member, coupon, order):
    # 무료배송 상품 30,000원이면 쿠폰을 쓸 수 있다
    m = member()
    c = coupon(m, "FIXED", amount=5000)
    o = order(m, [(30000, "GENERAL")], shipping=0)
    r = api.post(f"/orders/{o['orderId']}/coupon", {"couponId": c})
    (Expect()
     .true("정액 5,000원 쿠폰", coupon_in(o, c)["usable"], usable_text(coupon_in(o, c)), "사용 가능")
     .equal("자동 선택 쿠폰", o["autoSelectedCouponId"], c, fmt=str)
     .status("쿠폰 적용", r, 200)
     .equal("쿠폰 할인", r.body.get("couponDiscount"), 5000)
     .equal("최종 결제 금액", r.body.get("payAmount"), 25000)
     .done())


def test_TC_002_free_shipping_29999_coupon_unusable(api, member, coupon, order):
    # 무료배송 상품 29,999원이면 쿠폰을 쓸 수 없다
    m = member()
    c = coupon(m, "FIXED", amount=5000)
    o = order(m, [(29999, "GENERAL")], shipping=0)
    r = api.post(f"/orders/{o['orderId']}/coupon", {"couponId": c})
    after = api.get(f"/orders/{o['orderId']}").body
    (Expect()
     .equal("자동 선택 쿠폰", o["autoSelectedCouponId"], None, fmt=str)
     .true("API 쿠폰 적용 응답", r.status >= 400, r.brief(), "거부")
     .equal("쿠폰 할인", after["couponDiscount"], 0)
     .equal("최종 결제 금액", after["payAmount"], 29999)
     .done())


def test_TC_003_26999_plus_shipping_3000_coupon_unusable(api, member, coupon, order):
    # 상품 26,999원 + 배송비 3,000원(합계 29,999원)이면 쿠폰을 쓸 수 없다
    m = member()
    c = coupon(m, "FIXED", amount=5000)
    o = order(m, [(26999, "GENERAL")], shipping=3000)
    r = api.post(f"/orders/{o['orderId']}/coupon", {"couponId": c})
    after = api.get(f"/orders/{o['orderId']}").body
    (Expect()
     .true("쿠폰 적용 응답", r.status >= 400, r.brief(), "거부")
     .equal("자동 선택 쿠폰", o["autoSelectedCouponId"], None, fmt=str)
     .equal("쿠폰 할인", after["couponDiscount"], 0)
     .equal("최종 결제 금액", after["payAmount"], 29999)
     .done())


def _usable_and_applied(api, member, coupon, order, price, pay):
    m = member()
    c = coupon(m, "FIXED", amount=5000)
    o = order(m, [(price, "GENERAL")], shipping=3000)
    r = api.post(f"/orders/{o['orderId']}/coupon", {"couponId": c})
    return (Expect()
            .equal("자동 선택 쿠폰", o["autoSelectedCouponId"], c, fmt=str)
            .status("쿠폰 적용", r, 200)
            .equal("쿠폰 할인", r.body.get("couponDiscount"), 5000)
            .equal("최종 결제 금액", r.body.get("payAmount"), pay)), coupon_in(o, c)


def test_TC_004_27000_plus_shipping_3000_coupon_usable(api, member, coupon, order):
    # 상품 27,000원 + 배송비 3,000원(합계 30,000원)이면 쿠폰을 쓸 수 있다
    e, c = _usable_and_applied(api, member, coupon, order, 27000, 25000)
    e.true("정액 5,000원 쿠폰", c["usable"], usable_text(c), "사용 가능").done()


def test_TC_005_27001_plus_shipping_3000_coupon_usable(api, member, coupon, order):
    # 상품 27,001원 + 배송비 3,000원(합계 30,001원)이면 쿠폰을 쓸 수 있다
    e, _ = _usable_and_applied(api, member, coupon, order, 27001, 25001)
    e.done()


def test_TC_006_points_do_not_affect_min_amount(api, member, coupon, order):
    # 무료배송 30,000원 주문에 포인트 5,000P를 함께 쓰면 최소 주문 금액은 포인트 차감 전 금액으로 판정해 쿠폰이 적용된다
    m = member(points=5000)
    c = coupon(m, "FIXED", amount=5000)
    o = order(m, [(30000, "GENERAL")], shipping=0)
    oid = o["orderId"]
    api.ok("POST", f"/orders/{oid}/coupon", {"couponId": c})
    r = api.post(f"/orders/{oid}/points", {"amount": 5000})
    (Expect()
     .status("포인트 입력", r, 200)
     .equal("포인트 입력 후 적용 쿠폰", r.body.get("appliedCouponId"), c, fmt=str)
     .equal("쿠폰 할인", r.body.get("couponDiscount"), 5000)
     .equal("포인트 사용", r.body.get("usedPoints"), 5000)
     .equal("최종 결제 금액", r.body.get("payAmount"), 20000)
     .done())
