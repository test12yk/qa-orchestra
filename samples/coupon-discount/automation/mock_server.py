#!/usr/bin/env python3
"""샘플 '쿠폰 할인' QA 서버(가짜). api-spec.md 대로 동작하고, 규칙은 개발문서 v0.3을 따른다.

개발문서와 정책·기획서가 다른 부분(최소 금액에 상품권 포함, 쿠폰별 maxDiscount, 만료 쿠폰 복구 등)도
개발문서대로 구현돼 있어서, 자동화가 그 차이를 Fail로 잡는지 확인하는 데 쓴다.

실행: python3 mock_server.py [--port 8765]   (표준 라이브러리만 사용)
"""
import json
import sys
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "sample-token"
MIN_ORDER = 30000
MIN_PAY = 100
EXCLUDED = {"GIFT_CARD", "E_COUPON"}
FMT = "%Y-%m-%dT%H:%M:%S"

LOCK = threading.Lock()
S = {}


def reset():
    S.clear()
    S.update(members={}, coupons={}, orders={}, clock=None, pg="approve", seq=0)


def nid(prefix):
    S["seq"] += 1
    return f"{prefix}-{S['seq']}"


def now():
    return S["clock"] or datetime.now().replace(microsecond=0)


class ApiError(Exception):
    def __init__(self, status, code, message=""):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message or code


# ---------- 계산 (개발문서 v0.3) ----------

def target_amount(o):
    # 제외 카테고리는 할인 계산에서 뺀다
    return sum(i["price"] for i in o["items"] if not i["cancelled"] and i["category"] not in EXCLUDED)


def total_price(o):
    # 최소 금액 판정: totalPrice = 상품 금액(제외 카테고리 포함) + 배송비
    return sum(i["price"] for i in o["items"] if not i["cancelled"]) + o["shippingFee"]


def discount_of(c, o):
    t = target_amount(o)
    if c["type"] == "RATE":
        d = min(int(t * c["rate"] / 100), c.get("maxDiscount") or 10 ** 12)  # 쿠폰별 maxDiscount, 원 단위 절사
    else:
        d = min(c["amount"], t)
    return max(0, min(d, total_price(o) - MIN_PAY))


def expired(c):
    return now() > c["expiresAt"]


def unusable_reason(c, o):
    if c["status"] == "USED":
        return "USED"
    if expired(c):
        return "EXPIRED"
    if total_price(o) < MIN_ORDER:
        return "MIN_ORDER_AMOUNT_NOT_MET"
    return None


def view(o):
    coupons = []
    for c in S["coupons"].values():
        if c["memberId"] != o["memberId"]:
            continue
        reason = unusable_reason(c, o)
        days = (c["expiresAt"].date() - now().date()).days
        coupons.append({"couponId": c["couponId"], "type": c["type"], "discount": discount_of(c, o),
                        "usable": reason is None, "reason": reason, "expiresAt": c["expiresAt"].strftime(FMT),
                        "daysLeft": days, "soonExpiring": 1 <= days <= 3 and not expired(c)})
    coupons.sort(key=lambda x: -x["discount"])
    usable = [x for x in coupons if x["usable"]]
    tp = total_price(o)
    return {"orderId": o["orderId"], "memberId": o["memberId"], "status": o["status"],
            "items": [dict(i) for i in o["items"]],
            "goodsAmount": tp - o["shippingFee"],
            "excludedAmount": sum(i["price"] for i in o["items"] if not i["cancelled"] and i["category"] in EXCLUDED),
            "shippingFee": o["shippingFee"], "totalPrice": tp,
            "appliedCouponId": o["couponId"], "couponDiscount": o["couponDiscount"], "usedPoints": o["usedPoints"],
            "payAmount": o["payAmount"] if o["status"] != "CREATED" else tp - o["couponDiscount"] - o["usedPoints"],
            "autoSelectedCouponId": usable[0]["couponId"] if usable else None,
            "coupons": coupons, "refundedAmount": o["refunded"], "pgApprovedAmount": o["pgApproved"]}


def recalc(o):
    if o["couponId"]:
        o["couponDiscount"] = discount_of(S["coupons"][o["couponId"]], o)
    base = total_price(o) - o["couponDiscount"]
    o["usedPoints"] = max(0, min(o["requestedPoints"], base - MIN_PAY))


# ---------- 처리 ----------

def get(d, k):
    if k not in d:
        raise ApiError(400, "INVALID_REQUEST", f"{k} 필요")
    return d[k]


def order(oid):
    if oid not in S["orders"]:
        raise ApiError(404, "ORDER_NOT_FOUND")
    return S["orders"][oid]


def handle(method, parts, body):
    if parts == ["test", "reset"] and method == "POST":
        reset()
        return 200, {}
    if parts == ["test", "members"] and method == "POST":
        m = {"memberId": nid("M"), "points": int(body.get("points", 0))}
        S["members"][m["memberId"]] = m
        return 201, {"memberId": m["memberId"]}
    if parts == ["test", "coupons"] and method == "POST":
        mid = get(body, "memberId")
        if mid not in S["members"]:
            raise ApiError(404, "MEMBER_NOT_FOUND")
        exp = body.get("expiresAt")
        c = {"couponId": nid("C"), "memberId": mid, "type": get(body, "type"), "amount": body.get("amount"),
             "rate": body.get("rate"), "maxDiscount": body.get("maxDiscount"), "status": "AVAILABLE", "usedOrderId": None,
             "expiresAt": datetime.strptime(exp, FMT) if exp else now() + timedelta(days=30)}
        S["coupons"][c["couponId"]] = c
        return 201, {"couponId": c["couponId"]}
    if parts == ["test", "clock"] and method == "POST":
        S["clock"] = datetime.strptime(body["now"], FMT) if body.get("now") else None
        return 200, {"now": now().strftime(FMT)}
    if parts == ["test", "pg"] and method == "POST":
        S["pg"] = get(body, "mode")
        return 200, {"mode": S["pg"]}

    if parts == ["orders"] and method == "POST":
        mid = get(body, "memberId")
        if mid not in S["members"]:
            raise ApiError(404, "MEMBER_NOT_FOUND")
        items = [{"index": n, "name": i.get("name", f"상품{n + 1}"), "price": int(i["price"]),
                  "category": i.get("category", "GENERAL"), "cancelled": False} for n, i in enumerate(get(body, "items"))]
        o = {"orderId": nid("O"), "memberId": mid, "items": items, "shippingFee": int(body.get("shippingFee", 0)),
             "status": "CREATED", "couponId": None, "couponDiscount": 0, "requestedPoints": 0, "usedPoints": 0,
             "payAmount": 0, "paidLeft": 0, "refunded": 0, "pgApproved": None}
        S["orders"][o["orderId"]] = o
        return 201, view(o)
    if len(parts) >= 2 and parts[0] == "orders":
        o = order(parts[1])
        rest = parts[2:]
        if not rest and method == "GET":
            return 200, view(o)
        if rest == ["coupon"] and method == "POST":
            if o["couponId"]:
                raise ApiError(409, "COUPON_ALREADY_APPLIED")
            cid = get(body, "couponId")
            c = S["coupons"].get(cid)
            if not c or c["memberId"] != o["memberId"]:
                raise ApiError(404, "COUPON_NOT_FOUND")
            if c["status"] == "USED":
                raise ApiError(400, "COUPON_NOT_AVAILABLE")
            if expired(c):
                raise ApiError(400, "COUPON_EXPIRED")
            if total_price(o) < MIN_ORDER:
                raise ApiError(400, "MIN_ORDER_AMOUNT_NOT_MET")
            o["couponId"] = cid
            recalc(o)
            return 200, view(o)
        if rest == ["coupon"] and method == "DELETE":
            o["couponId"], o["couponDiscount"] = None, 0
            recalc(o)
            return 200, view(o)
        if rest == ["points"] and method == "POST":
            amt = int(get(body, "amount"))
            if amt > S["members"][o["memberId"]]["points"]:
                raise ApiError(400, "INSUFFICIENT_POINTS")
            o["requestedPoints"] = amt
            recalc(o)
            return 200, view(o)
        if rest == ["pay"] and method == "POST":
            if o["status"] != "CREATED":
                raise ApiError(409, "ALREADY_PAID")
            c = S["coupons"].get(o["couponId"]) if o["couponId"] else None
            if c and c["status"] == "USED":
                raise ApiError(409, "COUPON_ALREADY_USED")
            if S["pg"] == "decline":
                raise ApiError(402, "PG_DECLINED")
            pay = total_price(o) - o["couponDiscount"] - o["usedPoints"]
            if c:
                c["status"], c["usedOrderId"] = "USED", o["orderId"]  # 결제 승인 완료 후 USED
            S["members"][o["memberId"]]["points"] -= o["usedPoints"]
            o.update(status="PAID", payAmount=pay, paidLeft=pay, pgApproved=pay)
            return 200, {"orderId": o["orderId"], "status": "PAID", "approvedAmount": pay}
        if rest == ["cancel"] and method == "POST":
            if o["status"] not in ("PAID", "PARTIALLY_CANCELLED"):
                raise ApiError(409, "NOT_CANCELLABLE")
            live = [i for i in o["items"] if not i["cancelled"]]
            idx = body.get("itemIndexes")
            targets = [i for i in live if i["index"] in idx] if idx else live
            if not targets:
                raise ApiError(400, "INVALID_REQUEST", "취소할 상품 없음")
            if len(targets) == len(live):
                refund = o["paidLeft"]  # 마지막 취소는 남은 결제 금액 전부
            else:
                goods = sum(i["price"] for i in o["items"])
                refund = sum(i["price"] - int(o["couponDiscount"] * i["price"] / goods) for i in targets)
                refund = min(refund, o["paidLeft"])
            for i in targets:
                i["cancelled"] = True
            o["paidLeft"] -= refund
            o["refunded"] += refund
            o["status"] = "CANCELLED" if len(targets) == len(live) else "PARTIALLY_CANCELLED"
            if not idx and o["couponId"]:
                # 개발문서: 전체 취소 시에만 AVAILABLE로 복구 (유효기간 조건 없음)
                c = S["coupons"][o["couponId"]]
                c["status"], c["usedOrderId"] = "AVAILABLE", None
            return 200, {"orderId": o["orderId"], "status": o["status"], "refundAmount": refund}

    if len(parts) == 2 and parts[0] == "coupons" and method == "GET":
        c = S["coupons"].get(parts[1])
        if not c:
            raise ApiError(404, "COUPON_NOT_FOUND")
        return 200, {**c, "expiresAt": c["expiresAt"].strftime(FMT)}
    if len(parts) == 2 and parts[0] == "members" and method == "GET":
        m = S["members"].get(parts[1])
        if not m:
            raise ApiError(404, "MEMBER_NOT_FOUND")
        return 200, dict(m)
    raise ApiError(404, "NOT_FOUND", f"{method} /{'/'.join(parts)}")


class Handler(BaseHTTPRequestHandler):
    def _do(self, method):
        if self.headers.get("X-QA-Token") != TOKEN:
            return self._send(401, {"code": "UNAUTHORIZED", "message": "X-QA-Token 필요"})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        except json.JSONDecodeError:
            return self._send(400, {"code": "INVALID_JSON", "message": "JSON 본문 오류"})
        parts = [p for p in self.path.split("?")[0].split("/") if p]
        try:
            with LOCK:
                status, data = handle(method, parts, body)
        except ApiError as e:
            status, data = e.status, {"code": e.code, "message": e.message}
        self._send(status, data)

    def _send(self, status, data):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._do("GET")

    def do_POST(self):
        self._do("POST")

    def do_DELETE(self):
        self._do("DELETE")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8765
    reset()
    print(f"샘플 쿠폰 QA 서버: http://127.0.0.1:{port} (토큰 {TOKEN})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
