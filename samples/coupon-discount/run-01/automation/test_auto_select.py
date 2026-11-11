from qa_api import Expect, hold


def test_TC_030_auto_select_uses_actual_discount_with_gift_card(api, member, coupon, order):
    # 상품권이 섞여 정률 10% 실제 할인액(4,500원)이 정액 5,000원보다 작으면 정액 쿠폰이 자동 선택된다
    m = member()
    coupon(m, "RATE", rate=10, maxDiscount=10000)
    fixed = coupon(m, "FIXED", amount=5000)
    o = order(m, [(45000, "GENERAL"), (60000, "GIFT_CARD")], shipping=3000)
    r = api.post(f"/orders/{o['orderId']}/coupon", {"couponId": o["autoSelectedCouponId"]}) if o["autoSelectedCouponId"] else None
    (Expect()
     .equal("자동 선택 쿠폰", o["autoSelectedCouponId"], fixed, fmt=str)
     .equal("쿠폰 할인", r.body.get("couponDiscount") if r else 0, 5000)
     .equal("최종 결제 금액", r.body.get("payAmount") if r else o["payAmount"], 103000)
     .done())


def test_TC_031_switch_to_rate_recalculates(api, member, coupon, order):
    # 자동 선택된 정액 쿠폰을 정률 10% 쿠폰으로 바꾸면 할인이 4,000원으로 다시 계산된다
    m = member()
    rate = coupon(m, "RATE", rate=10, maxDiscount=10000)
    fixed = coupon(m, "FIXED", amount=5000)
    o = order(m, [(40000, "GENERAL")], shipping=3000)
    oid = o["orderId"]
    api.ok("POST", f"/orders/{oid}/coupon", {"couponId": o["autoSelectedCouponId"] or fixed})
    api.ok("DELETE", f"/orders/{oid}/coupon")
    r = api.post(f"/orders/{oid}/coupon", {"couponId": rate})
    (Expect()
     .equal("주문서 자동 선택 쿠폰(정액 5,000원)", o["autoSelectedCouponId"], fixed, fmt=str)
     .status("정률 쿠폰으로 변경", r, 200)
     .equal("선택 쿠폰", r.body.get("appliedCouponId"), rate, fmt=str)
     .equal("쿠폰 할인", r.body.get("couponDiscount"), 4000)
     .equal("최종 결제 금액", r.body.get("payAmount"), 39000)
     .done())
    hold("할인 재계산은 정상(-4,000원, 39,000원). \"최대 할인 적용됨\" 라벨 표시 여부는 화면 확인 + Q10 답 필요")


def test_TC_032_tie_selects_one(api, member, coupon, order):
    # 정률 10%와 정액 5,000원의 할인액이 5,000원으로 같으면 둘 중 하나가 자동 선택되고 할인은 5,000원이다
    m = member()
    rate = coupon(m, "RATE", rate=10, maxDiscount=10000)
    fixed = coupon(m, "FIXED", amount=5000)
    o = order(m, [(50000, "GENERAL")], shipping=3000)
    auto = o["autoSelectedCouponId"]
    e = Expect().true("자동 선택 쿠폰", auto in (rate, fixed), str(auto), "정률 10% 또는 정액 5,000원 1장")
    e.done()
    r = api.post(f"/orders/{o['orderId']}/coupon", {"couponId": auto})
    if r.body.get("couponDiscount") == 5300:
        hold("정률 할인 -5,300원으로 동률 아님 — Q9 답(할인 대상에 배송비 포함 여부) 필요")
    (Expect()
     .equal("쿠폰 할인", r.body.get("couponDiscount"), 5000)
     .equal("최종 결제 금액", r.body.get("payAmount"), 48000)
     .done())
    hold(f"{'정률 10%' if auto == rate else '정액 5,000원'} 쿠폰이 자동 선택됨 — Q10 답(동률 기준) 필요")


def test_TC_033_list_sorted_by_discount(api, member, coupon, order):
    # 쿠폰 목록은 할인액이 큰 순(7,000원 → 5,000원 → 3,000원)으로 정렬된다
    m = member()
    f3 = coupon(m, "FIXED", amount=3000)
    rate = coupon(m, "RATE", rate=10, maxDiscount=10000)
    f5 = coupon(m, "FIXED", amount=5000)
    o = order(m, [(70000, "GENERAL")], shipping=3000)
    names = {rate: "정률 10%", f5: "정액 5,000원", f3: "정액 3,000원"}
    got = [f"{names[c['couponId']]}({c['discount']:,}원)" for c in o["coupons"]]
    (Expect()
     .equal("목록 순서", " → ".join(got), "정률 10%(7,000원) → 정액 5,000원(5,000원) → 정액 3,000원(3,000원)", fmt=str)
     .done())
