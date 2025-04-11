from common.types import (
    JSONRPCResponse,                 # JSON‑RPC 규격의 표준 응답 모델
    ContentTypeNotSupportedError,    # 지원하지 않는 콘텐츠(모달리티) 에러
    UnsupportedOperationError,       # 아직 구현되지 않은 기능 에러
)
from typing import List


# ---------------------------------------------------------------------
# 모달리티(출력 형식) 호환성 검사
# ---------------------------------------------------------------------
def are_modalities_compatible(
    server_output_modes: List[str],  # 서버가 지원하는 출력 형식 목록
    client_output_modes: List[str],  # 클라이언트가 요청한 출력 형식 목록
) -> bool:
    """
    ✔️ 규칙
    1. 클라이언트나 서버 둘 중 하나라도 “목록이 비어 있거나 None”이면
       → 제한이 없는 것으로 간주 → 호환성 TRUE.
    2. 두 목록 모두 비어 있지 않다면
       → **교집합이 하나라도** 있으면 호환성 TRUE.
    """

    # 클라이언트가 특별히 원하는 형식이 없으면 무조건 호환
    if client_output_modes is None or len(client_output_modes) == 0:
        return True

    # 서버가 “어떤 형식이든 OK”라고 명시한 경우도 호환
    if server_output_modes is None or len(server_output_modes) == 0:
        return True

    # 두 목록에 공통 요소가 하나라도 있으면 호환
    return any(x in server_output_modes for x in client_output_modes)


# ---------------------------------------------------------------------
# 에러 응답 헬퍼
# ---------------------------------------------------------------------
def new_incompatible_types_error(request_id):
    """
    서버·클라이언트 모달리티가 호환되지 않을 때
    → ContentTypeNotSupportedError 로 JSON‑RPC 에러 응답 생성
    """
    return JSONRPCResponse(id=request_id, error=ContentTypeNotSupportedError())


def new_not_implemented_error(request_id):
    """
    아직 구현되지 않은 기능 호출 시
    → UnsupportedOperationError 로 JSON‑RPC 에러 응답 생성
    """
    return JSONRPCResponse(id=request_id, error=UnsupportedOperationError())