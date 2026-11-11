from qa_api import Expect, hold


def _apply(api, member, coupon, order, kind, items, shipping, points=0, member_points=0, **coupon_kw):
    m = member(points=member_points)
    c = coupon(m, kind, **coupon_kw)
    oid = order(m, items, shipping=shipping)["orderId"]
    r = api.post(f"/orders/{oid}/coupon", {"couponId": c})
    if points:
        r = api.post(f"/orders/{oid}/points", {"amount": points})
    return oid, r


def test_TC_017_rate_10_on_50000(api, member, coupon, order):
    # 상품 50,000원에 정률 10% 쿠폰을 쓰면 5,000원 할인된다
    _, r = _apply(api, member, coupon, order, "RATE", [(50000, "GENERAL")], 3000, rate=10, maxDiscount=10000)
    if r.body.get("couponDiscount") == 5300:
        hold("쿠폰 할인 -5,300원(최종 47,700원) — Q9 답(할인 대상에 배송비 포함 여부) 필요")
    (Expect()
     .status("쿠폰 적용", r, 200)
     .equal("쿠폰 할인", r.body.get("couponDiscount"), 5000)
     .equal("배송비", r.body.get("shippingFee"), 3000)
     .equal("최종 결제 금액", r.body.get("payAmount"), 48000)
     .done())


def test_TC_018_fixed_5000_on_40000(api, member, coupon, order):
    # 상품 40,000원에 정액 5,000원 쿠폰을 쓰면 5,000원 할인된다
    _, r = _apply(api, member, coupon, order, "FIXED", [(40000, "GENERAL")], 3000, amount=5000)
    (Expect()
     .status("쿠폰 적용", r, 200)
     .equal("쿠폰 할인", r.body.get("couponDiscount"), 5000)
     .equal("최종 결제 금액", r.body.get("payAmount"), 38000)
     .done())


def test_TC_019_rate_10_on_99990_below_cap(api, member, coupon, order):
    # 상품 99,990원에 정률 10% 쿠폰을 쓰면 한도 아래인 9,999원 할인된다
    _, r = _apply(api, member, coupon, order, "RATE", [(99990, "GENERAL")], 3000, rate=10, maxDiscount=10000)
    if r.body.get("couponDiscount") == 10000 and r.body.get("payAmount") == 92990:
        hold("쿠폰 할인 -10,000원(최종 92,990원) — Q9 답(할인 대상에 배송비 포함 여부) 필요")
    (Expect()
     .status("쿠폰 적용", r, 200)
     .equal("쿠폰 할인", r.body.get("couponDiscount"), 9999)
     .equal("최종 결제 금액", r.body.get("payAmount"), 92991)
     .done())


def test_TC_020_rate_10_on_100000_equals_cap(api, member, coupon, order):
    # 상품 100,000원에 정률 10% 쿠폰을 쓰면 한도와 같은 10,000원 할인된다
    _, r = _apply(api, member, coupon, order, "RATE", [(100000, "GENERAL")], 3000, rate=10, maxDiscount=10000)
    (Expect()
     .status("쿠폰 적용", r, 200)
     .equal("쿠폰 할인", r.body.get("couponDiscount"), 10000)
     .equal("최종 결제 금액", r.body.get("payAmount"), 93000)
     .done())


def test_TC_021_rate_10_on_100010_stops_at_cap(api, member, coupon, order):
    # 상품 100,010원에 정률 10% 쿠폰을 쓰면 할인이 한도 10,000원에서 멈춘다
    _, r = _apply(api, member, coupon, order, "RATE", [(100010, "GENERAL")], 3000, rate=10, maxDiscount=10000)
    (Expect()
     .status("쿠폰 적용", r, 200)
     .equal("쿠폰 할인", r.body.get("couponDiscount"), 10000)
     .equal("최종 결제 금액", r.body.get("payAmount"), 93010)
     .done())


def test_TC_022_coupon_max_15000_capped_by_policy(api, member, coupon, order):
    # maxDiscount 15,000원으로 설정된 정률 10% 쿠폰을 상품 200,000원에 쓰면 할인은 정책 한도 10,000원을 넘지 않는다
    _, r = _apply(api, member, coupon, order, "RATE", [(200000, "GENERAL")], 3000, rate=10, maxDiscount=15000)
    if r.body.get("couponDiscount") == 15000:
        hold("쿠폰 할인 -15,000원(최종 188,000원) — Q2 답(정책 일괄 한도 vs 쿠폰별 maxDiscount) 필요")
    (Expect()
     .status("쿠폰 적용", r, 200)
     .equal("쿠폰 할인", r.body.get("couponDiscount"), 10000)
     .equal("최종 결제 금액", r.body.get("payAmount"), 193000)
     .done())


def test_TC_023_rate_15_truncates_below_won(api, member, coupon, order):
    # 정률 15% 쿠폰 할인액 4,500.75원은 원 단위 미만을 절사해 4,500원이 된다
    _, r = _apply(api, member, coupon, order, "RATE", [(30005, "GENERAL")], 3000, rate=15, maxDiscount=10000)
    if r.body.get("couponDiscount") == 4950:
        hold("쿠폰 할인 -4,950원(최종 28,055원) — Q9 답(할인 대상에 배송비 포함 여부) 필요")
    (Expect()
     .status("쿠폰 적용", r, 200)
     .equal("쿠폰 할인(-4,501원 반올림 아님)", r.body.get("couponDiscount"), 4500)
     .equal("최종 결제 금액", r.body.get("payAmount"), 28505)
     .done())


def test_TC_024_rate_with_gift_card_and_points(api, member, coupon, order):
    # 정률 10% 쿠폰 + 상품권 혼합 + 포인트 5,000P를 함께 쓰면 일반 상품 기준 한도 10,000원 할인 후 포인트를 뺀다
    _, r = _apply(api, member, coupon, order, "RATE", [(120000, "GENERAL"), (50000, "GIFT_CARD")], 3000,
                  points=5000, member_points=5000, rate=10, maxDiscount=10000)
    (Expect()
     .status("포인트 입력", r, 200)
     .equal("쿠폰 할인", r.body.get("couponDiscount"), 10000)
     .equal("포인트 사용", r.body.get("usedPoints"), 5000)
     .equal("최종 결제 금액", r.body.get("payAmount"), 158000)
     .done())


def _pay_with_points(api, member, coupon, order, points):
    m = member(points=30000)
    c = coupon(m, "FIXED", amount=5000)
    oid = order(m, [(30000, "GENERAL")], shipping=0)["orderId"]
    api.ok("POST", f"/orders/{oid}/coupon", {"couponId": c})
    sheet = api.post(f"/orders/{oid}/points", {"amount": points})
    return oid, sheet


def test_TC_025_pay_amount_101(api, member, coupon, order):
    # 쿠폰 적용 후 25,000원에 포인트 24,899P를 쓰면 결제 금액 101원으로 결제된다
    oid, sheet = _pay_with_points(api, member, coupon, order, 24899)
    pay = api.post(f"/orders/{oid}/pay")
    (Expect()
     .equal("최종 결제 금액", sheet.body.get("payAmount"), 101)
     .status("결제 승인", pay, 200)
     .equal("결제 승인 금액", pay.body.get("approvedAmount"), 101)
     .done())


def test_TC_026_pay_amount_100(api, member, coupon, order):
    # 쿠폰 적용 후 25,000원에 포인트 24,900P를 쓰면 결제 금액 100원으로 결제된다
    oid, sheet = _pay_with_points(api, member, coupon, order, 24900)
    pay = api.post(f"/orders/{oid}/pay")
    (Expect()
     .equal("최종 결제 금액", sheet.body.get("payAmount"), 100)
     .status("결제 승인", pay, 200)
     .equal("결제 승인 금액", pay.body.get("approvedAmount"), 100)
     .done())


def test_TC_027_pay_amount_never_below_100(api, member, coupon, order):
    # 쿠폰 적용 후 25,000원에 포인트 24,901P를 넣어도 결제 금액이 100원 아래로 내려가지 않는다
    oid, sheet = _pay_with_points(api, member, coupon, order, 24901)
    after = api.get(f"/orders/{oid}").body
    pay_amount = after["payAmount"]
    (Expect()
     .not_equal("최종 결제 금액", pay_amount, 99)
     .true("최종 결제 금액", pay_amount >= 100, f"{pay_amount:,}원", "100원 이상")
     .done())
    how = (f"포인트 입력 거부({sheet.status} {sheet.code})" if sheet.status >= 400
           else f"포인트 {after['usedPoints']:,}P만 사용, 결제 금액 {pay_amount:,}원")
    hold(f"처리 방식: {how} — Q7 답(처리 방식·문구) 필요")
