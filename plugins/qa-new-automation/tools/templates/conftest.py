"""스위트 공통 준비. `api` 픽스처는 그대로 두고, 테스트 데이터 준비 픽스처는 아래에 API 문서대로 추가한다."""
import pytest

from qa_api import Api


@pytest.fixture(scope="session")
def api():
    return Api()


# ---- 테스트 데이터 준비 (API 문서의 준비용 API로 작성) ----
