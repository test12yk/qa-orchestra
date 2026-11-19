"""회귀 자동화 — 주문·결제(결제 금액·포인트·PG 승인)."""
from qa_api import Expect


def pt(v):
    return f"{v:,}P" if isinstance(v, (int, float)) else ("없음" if v is None else str(v))


def test_RG_0008_pay_amount_goods_plus_shipping(api, member, order):
    # 결제 금액은 상품 금액 합계에 배송비를 더한 값이다
    o = order(member(), [(25000, "GENERAL")], shipping=3000)
    sheet = api.get(f"/orders/{o['orderId']}")
    (Expect()
     .status("주문서 조회", sheet, 200)
     .equal("최종 결제 금액", sheet.body.get("payAmount"), 28000)
     .done())


def test_RG_0010_points_3000_reduce_pay_and_balance(api, member, order):
    # 포인트 3,000P를 쓰면 결제 금액이 3,000원 줄고 잔액도 줄어든다
    m = member(points=5000)
    oid = order(m, [(20000, "GENERAL")], shipping=3000)["orderId"]
    api.ok("POST", f"/orders/{oid}/points", {"amount": 3000})
    paid = api.post(f"/orders/{oid}/pay")
    (Expect()
     .status("결제", paid, 200)
     .equal("최종 결제 금액", api.get(f"/orders/{oid}").body.get("payAmount"), 20000)
     .equal("포인트 잔액", api.get(f"/members/{m}").body.get("points"), 2000, fmt=pt)
     .done())


def test_RG_0011_points_over_balance_capped(api, member, order):
    # 보유 포인트보다 많이 입력하면 보유 포인트까지만 쓰인다
    m = member(points=1200)
    # 주문 금액은 TC에 없어 포인트보다 넉넉한 금액으로 준비
    oid = order(m, [(20000, "GENERAL")], shipping=3000)["orderId"]
    r = api.post(f"/orders/{oid}/points", {"amount": 5000})
    (Expect()
     .true("포인트 사용 응답", r.status == 200, r.brief(), "200 성공")
     .equal("사용 포인트", r.body.get("usedPoints"), 1200, fmt=pt)
     .done())


def test_RG_0014_pg_approved_amount_equals_pay_amount(api, member, order, pg):
    # 카드 결제 승인 금액은 주문서 최종 결제 금액과 같다
    pg("approve")
    oid = order(member(), [(20000, "GENERAL")], shipping=3000)["orderId"]
    paid = api.post(f"/orders/{oid}/pay")
    after = api.get(f"/orders/{oid}").body
    (Expect()
     .status("결제", paid, 200)
     .equal("승인 금액", paid.body.get("approvedAmount"), 23000)
     .equal("주문 상태", after.get("status"), "PAID", fmt=str)
     .done())
