"""스위트 공통 준비. `api` 픽스처는 그대로 두고, 테스트 데이터 준비 픽스처는 아래에 API 문서대로 추가한다."""
import pytest

from qa_api import Api


@pytest.fixture(scope="session")
def api():
    return Api()


# ---- 테스트 데이터 준비 (API 문서의 준비용 API로 작성) ----

@pytest.fixture
def member(api):
    """포인트 보유량을 정해 테스트 회원을 만든다: member(points=5000)"""
    def make(points=0):
        return api.ok("POST", "/test/members", {"points": points})["memberId"]
    return make


@pytest.fixture
def coupon(api):
    """쿠폰 발급: coupon(m, "FIXED", amount=5000) / coupon(m, "RATE", rate=10, maxDiscount=10000, expiresAt=...)"""
    def make(member_id, type, **kw):
        return api.ok("POST", "/test/coupons", {"memberId": member_id, "type": type, **kw})["couponId"]
    return make


@pytest.fixture
def order(api):
    """주문서: order(m, [(40000, "GENERAL"), (20000, "GIFT_CARD")], shipping=3000)"""
    def make(member_id, items, shipping=0):
        body = {"memberId": member_id, "shippingFee": shipping,
                "items": [{"name": f"상품{n + 1}", "price": p, "category": cat} for n, (p, cat) in enumerate(items)]}
        return api.ok("POST", "/orders", body).body
    return make


@pytest.fixture
def paid_order(api, order):
    """쿠폰(있으면)·포인트(있으면)를 적용해 결제까지 끝낸 주문: paid_order(m, items, shipping, coupon_id=None, points=0)"""
    def make(member_id, items, shipping=0, coupon_id=None, points=0):
        o = order(member_id, items, shipping)
        oid = o["orderId"]
        if coupon_id:
            api.ok("POST", f"/orders/{oid}/coupon", {"couponId": coupon_id})
        if points:
            api.ok("POST", f"/orders/{oid}/points", {"amount": points})
        api.ok("POST", f"/orders/{oid}/pay")
        return api.ok("GET", f"/orders/{oid}").body
    return make


@pytest.fixture
def clock(api):
    """서버 시각 고정: clock("2026-11-10T23:59:58"). 테스트가 끝나면 실제 시각으로 되돌린다."""
    def set_(now):
        api.ok("POST", "/test/clock", {"now": now})
    yield set_
    api.ok("POST", "/test/clock", {"now": None})


@pytest.fixture
def pg(api):
    """다음 결제의 PG 결과: pg("decline"). 끝나면 승인으로 되돌린다."""
    def set_(mode):
        api.ok("POST", "/test/pg", {"mode": mode})
    yield set_
    api.ok("POST", "/test/pg", {"mode": "approve"})


def coupon_in(sheet, coupon_id):
    """주문서 쿠폰 목록에서 한 장 찾기"""
    return next((c for c in sheet["coupons"] if c["couponId"] == coupon_id), None)


def usable_text(c):
    """쿠폰 목록 한 장의 사용 가능 여부를 이슈내용용 말로"""
    if c is None:
        return "목록에 없음"
    return "사용 가능" if c["usable"] else f"사용 불가({c['reason']})"
