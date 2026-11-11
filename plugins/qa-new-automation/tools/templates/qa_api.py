"""QA 오케스트라 API 자동화 도우미 (표준 라이브러리 + pytest).

테스트 코드는 이 파일의 `Api`(요청), `Expect`(기대 결과 비교), `hold`/`manual`(판정 보류·자동화 제외)만 쓴다.
서버 주소·인증은 같은 폴더의 automation.json 과 환경변수에서 읽는다. 토큰은 파일에 쓰지 않는다.
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def settings():
    s = json.loads((HERE / "automation.json").read_text(encoding="utf-8"))
    s["base_url"] = os.environ.get("QA_BASE_URL") or s.get("base_url", "")
    return s


class Resp:
    def __init__(self, status, body):
        self.status, self.body = status, body

    @property
    def code(self):
        return self.body.get("code") if isinstance(self.body, dict) else None

    def __getitem__(self, k):
        return self.body[k]

    def brief(self):
        """이슈내용용 짧은 설명: '200 성공' / '400 MIN_ORDER_AMOUNT_NOT_MET'"""
        return f"{self.status} {self.code}" if self.code else f"{self.status} {'성공' if self.status < 400 else '오류'}"

    def __repr__(self):
        return f"{self.status} {json.dumps(self.body, ensure_ascii=False)[:300]}"


class Api:
    def __init__(self):
        s = settings()
        if not s["base_url"]:
            pytest.exit("서버 주소가 없습니다: automation.json의 base_url 또는 환경변수 QA_BASE_URL", returncode=3)
        self.base = s["base_url"].rstrip("/")
        self.timeout = s.get("timeout", 10)
        self.headers = {"Content-Type": "application/json", "Accept": "application/json"}
        auth = s.get("auth") or {}
        if auth.get("header"):
            token = os.environ.get(auth.get("env", ""), auth.get("sample_token", ""))
            if token:
                self.headers[auth["header"]] = auth.get("prefix", "") + token

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
                return Resp(r.status, json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return Resp(e.code, json.loads(raw) if raw else {})
            except ValueError:
                return Resp(e.code, {"raw": raw.decode(errors="replace")[:300]})

    def get(self, path):
        return self.call("GET", path)

    def post(self, path, body=None):
        return self.call("POST", path, body if body is not None else {})

    def delete(self, path):
        return self.call("DELETE", path)

    def ok(self, method, path, body=None, status=(200, 201)):
        """준비 단계용: 실패하면 테스트 결함이 아니라 '준비 실패'로 멈춘다."""
        r = self.call(method, path, body)
        if r.status not in ((status,) if isinstance(status, int) else status):
            raise SetupError(f"{method} {path} → {r}")
        return r


class SetupError(Exception):
    """테스트 데이터 준비 실패. 결과는 Fail이 아니라 자동화 오류로 기록된다."""


def won(v):
    return f"{v:,}원" if isinstance(v, (int, float)) else str(v)


class Expect:
    """TC 기대 결과 한 줄씩 비교하고, 틀린 줄을 모두 모아 한 번에 실패시킨다.
    실패 메시지는 TC 시트 이슈내용에 그대로 들어가므로 '<무엇>: 실제 <값> (기대 <값>)' 꼴로 남는다."""

    def __init__(self):
        self.misses = []

    def equal(self, what, actual, expected, fmt=won):
        if actual != expected:
            self.misses.append(f"{what}: 실제 {fmt(actual)} (기대 {fmt(expected)})")
        return self

    def not_equal(self, what, actual, unexpected, fmt=won):
        if actual == unexpected:
            self.misses.append(f"{what}: 실제 {fmt(actual)} (기대 {fmt(unexpected)} 아님)")
        return self

    def true(self, what, cond, actual, expected):
        """조건 비교. what은 명사(무엇), actual·expected는 사람이 읽는 말로: true("쿠폰 적용", r.status >= 400, r.brief(), "거부")"""
        if not cond:
            self.misses.append(f"{what}: 실제 {actual} (기대 {expected})")
        return self

    def status(self, what, resp, expected_status, expected_code=None):
        ok = resp.status == expected_status and (expected_code is None or resp.code == expected_code)
        if not ok:
            exp = f"{expected_status}" + (f" {expected_code}" if expected_code else "")
            got = f"{resp.status} {resp.code or ''}".rstrip()
            self.misses.append(f"{what}: 실제 {got} (기대 {exp})")
        return self

    def done(self):
        if self.misses:
            raise AssertionError("\n".join(self.misses))


def hold(reason):
    """TC가 '판정 보류'로 정한 결과가 나왔을 때. 결과 칸은 비우고 이슈내용에 이유를 남긴다."""
    pytest.skip(f"[판정 보류] {reason}")


def manual(reason):
    """자동화로 끝까지 확인할 수 없을 때(화면 확인 등). 결과 칸은 비워 둔다."""
    pytest.skip(f"[자동화 제외] {reason}")
