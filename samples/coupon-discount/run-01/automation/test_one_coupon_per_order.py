from qa_api import Expect


def test_TC_014_only_one_coupon_selected(api, member, coupon, order):
    # 쿠폰 2장을 보유해도 주문서에서는 1장만 적용된다
    m = member()
    rate = coupon(m, "RATE", rate=10, maxDiscount=10000)
    fixed = coupon(m, "FIXED", amount=5000)
    oid = order(m, [(80000, "GENERAL")], shipping=3000)["orderId"]
    api.ok("POST", f"/orders/{oid}/coupon", {"couponId": rate})
    # 화면에서 다른 쿠폰을 고르는 것 = 해제 후 적용
    api.ok("DELETE", f"/orders/{oid}/coupon")
    r = api.post(f"/orders/{oid}/coupon", {"couponId": fixed})
    (Expect()
     .status("정액 쿠폰 선택", r, 200)
     .equal("선택된 쿠폰", r.body.get("appliedCouponId"), fixed, fmt=str)
     .equal("쿠폰 할인", r.body.get("couponDiscount"), 5000)
     .equal("최종 결제 금액", r.body.get("payAmount"), 78000)
     .done())


def test_TC_015_second_coupon_rejected_409(api, member, coupon, order):
    # 쿠폰이 적용된 주문에 다른 쿠폰을 API로 적용하면 409 COUPON_ALREADY_APPLIED가 온다
    m = member()
    a = coupon(m, "FIXED", amount=5000)
    b = coupon(m, "RATE", rate=10, maxDiscount=10000)
    oid = order(m, [(40000, "GENERAL")], shipping=3000)["orderId"]
    api.ok("POST", f"/orders/{oid}/coupon", {"couponId": a})
    r = api.post(f"/orders/{oid}/coupon", {"couponId": b})
    after = api.get(f"/orders/{oid}").body
    (Expect()
     .status("쿠폰 B 적용", r, 409, "COUPON_ALREADY_APPLIED")
     .equal("적용 쿠폰", after["appliedCouponId"], a, fmt=str)
     .equal("쿠폰 할인", after["couponDiscount"], 5000)
     .equal("최종 결제 금액", after["payAmount"], 38000)
     .done())


def test_TC_016_same_coupon_twice_409(api, member, coupon, order):
    # 같은 쿠폰을 같은 주문에 두 번 적용 요청하면 두 번째는 409로 거부되고 할인은 한 번만 된다
    m = member()
    c = coupon(m, "FIXED", amount=5000)
    oid = order(m, [(40000, "GENERAL")], shipping=3000)["orderId"]
    r1 = api.post(f"/orders/{oid}/coupon", {"couponId": c})
    r2 = api.post(f"/orders/{oid}/coupon", {"couponId": c})
    after = api.get(f"/orders/{oid}").body
    (Expect()
     .status("첫 적용 요청", r1, 200)
     .status("두 번째 적용 요청", r2, 409, "COUPON_ALREADY_APPLIED")
     .equal("쿠폰 할인", after["couponDiscount"], 5000)
     .equal("최종 결제 금액", after["payAmount"], 38000)
     .done())
