from conftest import coupon_in
from qa_api import Expect

# 배지는 API의 soonExpiring·daysLeft로 확인한다. 화면 표시는 별도 확인(plan.json note).


def _coupon_on_sheet(member, coupon, order, clock, expires):
    clock("2026-11-10T12:00:00")  # 사전조건: 오늘 2026-11-10
    m = member()
    c = coupon(m, "FIXED", amount=5000, expiresAt=expires)
    return coupon_in(order(m, [(40000, "GENERAL")], shipping=3000), c)


def test_TC_034_d4_no_badge(api, member, coupon, order, clock):
    # 만료까지 4일 남은 쿠폰(D-4)에는 "곧 만료" 배지가 없다
    c = _coupon_on_sheet(member, coupon, order, clock, "2026-11-14T23:59:59")
    Expect().equal("\"곧 만료\" 배지(soonExpiring)", c["soonExpiring"], False, fmt=str).done()


def test_TC_035_d3_badge(api, member, coupon, order, clock):
    # 만료까지 3일 남은 쿠폰(D-3)에는 "곧 만료" D-3 배지가 붙는다
    c = _coupon_on_sheet(member, coupon, order, clock, "2026-11-13T23:59:59")
    (Expect()
     .equal("\"곧 만료\" 배지(soonExpiring)", c["soonExpiring"], True, fmt=str)
     .equal("D-day 표시(daysLeft)", c["daysLeft"], 3, fmt=lambda v: f"D-{v}")
     .done())


def test_TC_036_d1_badge(api, member, coupon, order, clock):
    # 만료까지 1일 남은 쿠폰(D-1)에는 "곧 만료" D-1 배지가 붙는다
    c = _coupon_on_sheet(member, coupon, order, clock, "2026-11-11T23:59:59")
    (Expect()
     .equal("\"곧 만료\" 배지(soonExpiring)", c["soonExpiring"], True, fmt=str)
     .equal("D-day 표시(daysLeft)", c["daysLeft"], 1, fmt=lambda v: f"D-{v}")
     .done())
