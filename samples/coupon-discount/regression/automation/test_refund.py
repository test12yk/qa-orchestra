"""회귀 자동화 — 취소·환불."""
from qa_api import Expect


def pt(v):
    return f"{v:,}P" if isinstance(v, (int, float)) else ("없음" if v is None else str(v))


def test_RG_0017_full_cancel_refunds_all(api, member, paid_order):
    # 결제 완료 주문을 전체 취소하면 결제 금액 전액이 환불된다
    o = paid_order(member(), [(25000, "GENERAL")], shipping=3000)
    r = api.post(f"/orders/{o['orderId']}/cancel", {})
    (Expect()
     .status("전체 취소", r, 200)
     .equal("주문 상태", r.body.get("status"), "CANCELLED", fmt=str)
     .equal("환불 금액", r.body.get("refundAmount"), 28000)
     .done())


def test_RG_0018_full_cancel_restores_points(api, member, paid_order):
    # 포인트를 쓴 주문을 전체 취소하면 쓴 포인트가 되돌아온다
    m = member(points=3000)
    # 주문 금액은 TC에 없어 포인트보다 넉넉한 금액으로 준비
    o = paid_order(m, [(20000, "GENERAL")], shipping=3000, points=3000)
    r = api.post(f"/orders/{o['orderId']}/cancel", {})
    (Expect()
     .status("전체 취소", r, 200)
     .equal("포인트 잔액", api.get(f"/members/{m}").body.get("points"), 3000, fmt=pt)
     .equal("환불 금액", r.body.get("refundAmount"), o["payAmount"])
     .done())


def test_RG_0019_partial_cancel_refunds_item_only(api, member, paid_order):
    # 상품 2개 중 1개를 부분 취소하면 그 상품 금액만 환불된다
    o = paid_order(member(), [(10000, "GENERAL"), (15000, "GENERAL")], shipping=0)
    r = api.post(f"/orders/{o['orderId']}/cancel", {"itemIndexes": [1]})
    a = api.get(f"/orders/{o['orderId']}").body["items"][0]
    (Expect()
     .status("부분 취소", r, 200)
     .equal("환불 금액", r.body.get("refundAmount"), 15000)
     .true("상품 A", not a["cancelled"], "취소됨", "결제 완료 그대로")
     .done())


def test_RG_0020_partial_cancel_below_free_shipping_deducts_fee(api, member, paid_order):
    # 부분 취소로 무료배송 기준 아래가 되면 배송비 3,000원을 빼고 환불한다
    o = paid_order(member(), [(20000, "GENERAL"), (15000, "GENERAL")], shipping=0)
    r = api.post(f"/orders/{o['orderId']}/cancel", {"itemIndexes": [1]})
    (Expect()
     .status("부분 취소", r, 200)
     .equal("환불 금액", r.body.get("refundAmount"), 12000)
     .done())
