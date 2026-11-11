from conftest import coupon_in
from qa_api import Expect, hold


def test_TC_007_rate_coupon_only_on_general_items(api, member, coupon, order):
    # 일반 상품 40,000원 + 상품권 20,000원 장바구니에 정률 10% 쿠폰을 쓰면 일반 상품에만 할인된다
    m = member()
    c = coupon(m, "RATE", rate=10, maxDiscount=10000)
    o = order(m, [(40000, "GENERAL"), (20000, "GIFT_CARD")], shipping=3000)
    r = api.post(f"/orders/{o['orderId']}/coupon", {"couponId": c})
    if r.body.get("couponDiscount") == 4300:
        hold("쿠폰 할인 -4,300원(최종 58,700원) — Q9 답(할인 대상에 배송비 포함 여부) 필요")
    (Expect()
     .status("쿠폰 적용", r, 200)
     .equal("쿠폰 할인", r.body.get("couponDiscount"), 4000)
     .equal("최종 결제 금액", r.body.get("payAmount"), 59000)
     .done())


def test_TC_008_fixed_coupon_with_e_coupon_item(api, member, coupon, order):
    # 일반 상품 35,000원 + e쿠폰 10,000원 장바구니에 정액 5,000원 쿠폰을 쓰면 5,000원 할인된다
    m = member()
    c = coupon(m, "FIXED", amount=5000)
    o = order(m, [(35000, "GENERAL"), (10000, "E_COUPON")], shipping=3000)
    r = api.post(f"/orders/{o['orderId']}/coupon", {"couponId": c})
    (Expect()
     .status("쿠폰 적용", r, 200)
     .equal("쿠폰 할인", r.body.get("couponDiscount"), 5000)
     .equal("최종 결제 금액", r.body.get("payAmount"), 43000)
     .done())


def test_TC_009_gift_card_excluded_from_min_amount_29999(api, member, coupon, order):
    # 일반 상품 26,999원 + 배송비 3,000원 + 상품권 10,000원이면 제외 상품을 뺀 29,999원 기준으로 쿠폰을 쓸 수 없다
    # TC 메모: API 문서대로(39,999원 전체로 검증) 적용되면 결함 후보로 보고 → hold 없이 Fail
    m = member()
    c = coupon(m, "FIXED", amount=5000)
    o = order(m, [(26999, "GENERAL"), (10000, "GIFT_CARD")], shipping=3000)
    r = api.post(f"/orders/{o['orderId']}/coupon", {"couponId": c})
    after = api.get(f"/orders/{o['orderId']}").body
    (Expect()
     .true("쿠폰 적용 응답", r.status >= 400, r.brief(), "거부(판정 기준 29,999원)")
     .equal("쿠폰 할인", after["couponDiscount"], 0)
     .equal("최종 결제 금액", after["payAmount"], 39999)
     .done())


def test_TC_010_gift_card_excluded_from_min_amount_30000(api, member, coupon, order):
    # 일반 상품 27,000원 + 배송비 3,000원 + 상품권 10,000원이면 제외 상품을 뺀 30,000원 기준으로 쿠폰을 쓸 수 있다
    m = member()
    c = coupon(m, "FIXED", amount=5000)
    o = order(m, [(27000, "GENERAL"), (10000, "GIFT_CARD")], shipping=3000)
    r = api.post(f"/orders/{o['orderId']}/coupon", {"couponId": c})
    (Expect()
     .status("쿠폰 적용", r, 200)
     .equal("쿠폰 할인", r.body.get("couponDiscount"), 5000)
     .equal("최종 결제 금액", r.body.get("payAmount"), 35000)
     .done())


def test_TC_011_gift_card_only_cart(api, member, coupon, order):
    # 상품권 50,000원만 담은 장바구니면 쿠폰을 쓸 수 없다
    # TC 메모: Q5가 API 방식이면 적용은 되고 할인 0원 → 판정 보류. 할인 0원으로 쿠폰이 USED 되면 결함
    m = member()
    rate = coupon(m, "RATE", rate=10, maxDiscount=10000)
    fixed = coupon(m, "FIXED", amount=5000)
    o = order(m, [(50000, "GIFT_CARD")], shipping=0)
    oid = o["orderId"]
    auto = o["autoSelectedCouponId"]
    if auto:  # 화면처럼 자동 선택된 쿠폰을 적용한 채로 결제
        api.ok("POST", f"/orders/{oid}/coupon", {"couponId": auto})
    pay = api.post(f"/orders/{oid}/pay")
    statuses = {cid: api.ok("GET", f"/coupons/{cid}")["status"] for cid in (rate, fixed)}
    e = (Expect()
         .status("결제", pay, 200)
         .equal("최종 결제 금액", pay.body.get("approvedAmount"), 50000)
         .equal("정률 10% 쿠폰 상태", statuses[rate], "AVAILABLE", fmt=str)
         .equal("정액 5,000원 쿠폰 상태", statuses[fixed], "AVAILABLE", fmt=str))
    e.done()
    if auto or any(c["usable"] for c in o["coupons"]):
        hold(f"쿠폰이 사용 가능으로 표시·자동 선택됨(할인 {coupon_in(o, auto)['discount'] if auto else 0:,}원), 결제 후 쿠폰은 AVAILABLE — Q5 답(최소 금액 판정 기준) 필요")
