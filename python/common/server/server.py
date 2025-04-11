# ---- 외부 라이브러리 & 타입 정의 ----
from starlette.applications import Starlette          # Starlette 애플리케이션 객체
from starlette.responses import JSONResponse          # JSON 응답 헬퍼
from sse_starlette.sse import EventSourceResponse     # SSE(Server‑Sent Events) 응답 헬퍼
from starlette.requests import Request                # HTTP 요청 객체

# 프로젝트 공통 타입(주로 Pydantic 모델) 가져오기
from common.types import (
    A2ARequest,                         # 최상위 JSON‑RPC 요청 타입
    JSONRPCResponse,                    # JSON‑RPC 응답 타입
    InvalidRequestError, JSONParseError,# 표준 JSON‑RPC 에러
    GetTaskRequest, CancelTaskRequest,  # 개별 메서드별 요청 모델
    SendTaskRequest,
    SetTaskPushNotificationRequest,
    GetTaskPushNotificationRequest,
    InternalError,                      # 서버 내부 오류용 에러 모델
    AgentCard,                          # 에이전트 메타 정보
    TaskResubscriptionRequest,          # 스트림 재구독 요청
    SendTaskStreamingRequest,
)
from pydantic import ValidationError
import json
from typing import AsyncIterable, Any
from common.server.task_manager import TaskManager    # 실제 비즈니스 로직 처리 객체

import logging
logger = logging.getLogger(__name__)


class A2AServer:
    """
    Starlette 기반 A2A(Agent-to-Agent) JSON-RPC 서버.

    • POST {endpoint}                : JSON-RPC 요청 처리
    • GET  /.well-known/agent.json   : AgentCard(메타데이터) 노출
    """

    def __init__(
        self,
        host: str = "0.0.0.0",                      # 바인딩할 호스트/IP
        port: int = 5000,                           # 바인딩할 포트
        endpoint: str = "/",                        # JSON‑RPC 엔드포인트
        agent_card: AgentCard | None = None,        # 에이전트 메타정보
        task_manager: TaskManager | None = None,    # 실제 작업을 처리할 매니저
    ):
        # 인스턴스 변수에 저장
        self.host = host
        self.port = port
        self.endpoint = endpoint
        self.task_manager = task_manager
        self.agent_card = agent_card

        # Starlette 애플리케이션 생성 및 라우팅 등록
        self.app = Starlette()
        self.app.add_route(self.endpoint, self._process_request, methods=["POST"])
        self.app.add_route(
            "/.well-known/agent.json", self._get_agent_card, methods=["GET"]
        )

    # -------------------- 서버 구동 --------------------
    def start(self):
        """uvicorn 으로 애플리케이션 실행"""
        if self.agent_card is None:
            raise ValueError("agent_card is not defined")

        if self.task_manager is None:
            raise ValueError("request_handler is not defined")

        import uvicorn
        uvicorn.run(self.app, host=self.host, port=self.port)

    # -------------------- 라우터 핸들러 --------------------
    def _get_agent_card(self, request: Request) -> JSONResponse:
        """GET /.well-known/agent.json → AgentCard JSON 반환"""
        return JSONResponse(self.agent_card.model_dump(exclude_none=True))

    async def _process_request(self, request: Request):
        """
        POST {endpoint} → JSON‑RPC 요청을 파싱하여
        TaskManager 로 전달하고, 응답을 생성한다.
        """
        try:
            body = await request.json()                       # JSON 본문 파싱
            json_rpc_request = A2ARequest.validate_python(body)  # Pydantic 검증

            # -------------------- 요청 타입별 분기 --------------------
            if isinstance(json_rpc_request, GetTaskRequest):
                result = await self.task_manager.on_get_task(json_rpc_request)

            elif isinstance(json_rpc_request, SendTaskRequest):
                result = await self.task_manager.on_send_task(json_rpc_request)

            elif isinstance(json_rpc_request, SendTaskStreamingRequest):
                result = await self.task_manager.on_send_task_subscribe(
                    json_rpc_request
                )

            elif isinstance(json_rpc_request, CancelTaskRequest):
                result = await self.task_manager.on_cancel_task(json_rpc_request)

            elif isinstance(json_rpc_request, SetTaskPushNotificationRequest):
                result = await self.task_manager.on_set_task_push_notification(
                    json_rpc_request
                )

            elif isinstance(json_rpc_request, GetTaskPushNotificationRequest):
                result = await self.task_manager.on_get_task_push_notification(
                    json_rpc_request
                )

            elif isinstance(json_rpc_request, TaskResubscriptionRequest):
                result = await self.task_manager.on_resubscribe_to_task(
                    json_rpc_request
                )

            else:
                # 예상치 못한 타입
                logger.warning(f"Unexpected request type: {type(json_rpc_request)}")
                raise ValueError(f"Unexpected request type: {type(request)}")

            # TaskManager 결과를 JSON 또는 SSE 응답으로 변환
            return self._create_response(result)

        except Exception as e:
            # 모든 예외를 공통 핸들러로 위임
            return self._handle_exception(e)

    # -------------------- 예외 처리 --------------------
    def _handle_exception(self, e: Exception) -> JSONResponse:
        """예외를 JSON‑RPC 에러 형태로 매핑해 400 응답을 반환"""
        if isinstance(e, json.decoder.JSONDecodeError):
            json_rpc_error = JSONParseError()

        elif isinstance(e, ValidationError):
            # Pydantic 검증 실패 → InvalidRequestError 로 래핑
            json_rpc_error = InvalidRequestError(data=json.loads(e.json()))

        else:
            # 예상치 못한 예외
            logger.error(f"Unhandled exception: {e}")
            json_rpc_error = InternalError()

        response = JSONRPCResponse(id=None, error=json_rpc_error)
        return JSONResponse(response.model_dump(exclude_none=True), status_code=400)

    # -------------------- 정상 응답 생성 --------------------
    def _create_response(self, result: Any) -> JSONResponse | EventSourceResponse:
        """
        TaskManager 가 반환한 결과를
        • JSONRPCResponse → JSONResponse
        • AsyncIterable    → SSE(EventSourceResponse)
        로 변환한다.
        """
        if isinstance(result, AsyncIterable):
            # ---- SSE 스트리밍 ----
            async def event_generator(result) -> AsyncIterable[dict[str, str]]:
                async for item in result:
                    # data 필드에 직렬화된 JSON‑RPC 응답을 담아 보냄
                    yield {"data": item.model_dump_json(exclude_none=True)}

            return EventSourceResponse(event_generator(result))

        elif isinstance(result, JSONRPCResponse):
            # ---- 일반 JSON‑RPC 응답 ----
            return JSONResponse(result.model_dump(exclude_none=True))

        else:
            logger.error(f"Unexpected result type: {type(result)}")
            raise ValueError(f"Unexpected result type: {type(result)}")
