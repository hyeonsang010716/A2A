import httpx                # 경량 HTTP 클라이언트 라이브러리
from common.types import (
    AgentCard,              # 에이전트 메타정보를 담는 Pydantic 모델
    A2AClientJSONError,     # JSON 파싱 오류를 감싸서 던지는 커스텀 예외
)
import json


class A2ACardResolver:
    """
    주어진 ‘기본 URL’(base_url)로부터
    `/.well-known/agent.json` 파일을 가져와
    AgentCard 객체로 변환해 주는 헬퍼 클래스
    """

    def __init__(self, base_url, agent_card_path="/.well-known/agent.json"):
        # 뒤에 슬래시가 두 번 붙는 것을 방지하기 위해 제거
        self.base_url = base_url.rstrip("/")
        # 앞쪽 슬래시가 두 번 붙는 것을 방지하기 위해 제거
        self.agent_card_path = agent_card_path.lstrip("/")

    def get_agent_card(self) -> AgentCard:
        """
        1. `base_url/agent_card_path` 위치에 GET 요청
        2. HTTP 오류가 있으면 예외 발생 (`raise_for_status()`)
        3. 응답 본문(JSON)을 AgentCard 모델에 매핑
        4. JSON 파싱 실패 시 A2AClientJSONError 예외로 감싸서 전달
        """
        with httpx.Client() as client:                       # 동기 HTTP 클라이언트 생성
            response = client.get(                           # GET 요청 전송
                f"{self.base_url}/{self.agent_card_path}"
            )
            response.raise_for_status()                      # 4xx/5xx 응답 시 예외 발생

            try:
                # JSON → AgentCard(Pydantic) 변환
                return AgentCard(**response.json())
            except json.JSONDecodeError as e:                # JSON 파싱 실패 시
                # 커스텀 예외로 감싸서 호출자에게 전달
                raise A2AClientJSONError(str(e)) from e
