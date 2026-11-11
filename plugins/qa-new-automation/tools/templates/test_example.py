"""작성 예시 — 스위트에 복사되지 않는다. TC 하나 = 테스트 함수 하나, 이름은 test_<TC 번호>_<짧은 설명>."""
from qa_api import Expect, hold

API = "/orders"


def test_TC_017_rate_coupon_10_percent(api, member, issue_coupon):
    # 사전조건: 정률 10% 쿠폰(maxDiscount 10,000원), 일반 상품 50,000원 (TC 그대로)
    coupon = issue_coupon(member, type="RATE", rate=10, maxDiscount=10000)
    order = api.ok("POST", API, {"memberId": member, "items": [{"price": 50000, "category": "GENERAL"}], "shippingFee": 0})
    # 절차: 쿠폰을 적용한다
    r = api.post(f"{API}/{order['orderId']}/coupon", {"couponId": coupon})
    # 기대 결과: TC의 expected 한 줄 = Expect 한 줄. 숫자는 TC에 적힌 값 그대로
    (Expect()
     .status("쿠폰 적용", r, 200)
     .equal("쿠폰 할인", r.body.get("couponDiscount"), 5000)
     .equal("최종 결제 금액", r.body.get("payAmount"), 45000)
     .done())


def test_TC_022_policy_cap(api, member, issue_coupon):
    # TC가 '판정 보류' 조건을 적어 두었으면 그 값이 나올 때 hold()로 남긴다(결과 칸 비움)
    coupon = issue_coupon(member, type="RATE", rate=10, maxDiscount=15000)
    order = api.ok("POST", API, {"memberId": member, "items": [{"price": 200000}], "shippingFee": 3000})
    r = api.post(f"{API}/{order['orderId']}/coupon", {"couponId": coupon})
    if r.body.get("couponDiscount") == 15000:
        hold("쿠폰 할인 -15,000원(최종 188,000원) — Q2 답(정책 일괄 한도 vs 쿠폰별 maxDiscount) 필요")
    Expect().equal("쿠폰 할인", r.body.get("couponDiscount"), 10000).done()
