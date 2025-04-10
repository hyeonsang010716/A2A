import httpx
from httpx_sse import connect_sse
from typing import Any, AsyncIterable

# 아래는 코드에서 사용하는 모델(데이터 클래스)들입니다.
# 실제 구현체는 보통 pydantic 등을 이용해 정의해두고, 이 코드를 통해 JSON-RPC 요청/응답에 사용됩니다.
from common.types import (
    AgentCard,
    GetTaskRequest,
    SendTaskRequest,
    SendTaskResponse,
    JSONRPCRequest,
    GetTaskResponse,
    CancelTaskResponse,
    CancelTaskRequest,
    SetTaskPushNotificationRequest,
    SetTaskPushNotificationResponse,
    GetTaskPushNotificationRequest,
    GetTaskPushNotificationResponse,
    A2AClientHTTPError,
    A2AClientJSONError,
    SendTaskStreamingRequest,
    SendTaskStreamingResponse,
)
import json


class A2AClient:
    """
    A2AClient 클래스는 주어진 URL 혹은 AgentCard 정보를 통해,
    HTTP 혹은 SSE(Server-Sent Events) 기반의 요청을 전송하고 응답을 받아오는 기능을 제공한다.
    """

    def __init__(self, agent_card: AgentCard = None, url: str = None):
        """
        초기화 메서드.
        agent_card 또는 url이 전달되지 않으면 오류를 발생시킨다.
        
        :param agent_card: AgentCard 객체 (이 안에 url 등 정보가 들어있다고 가정)
        :param url: 직접 문자열로 url을 전달 가능
        """
        if agent_card:
            self.url = agent_card.url
        elif url:
            self.url = url
        else:
            raise ValueError("Must provide either agent_card or url")

    async def send_task(self, payload: dict[str, Any]) -> SendTaskResponse:
        """
        JSON-RPC로 'task'를 전송하는 메서드.
        payload에 해당하는 데이터를 SendTaskRequest 형식으로 감싸서 서버로 전송하고,
        그 결과를 받아 SendTaskResponse 모델로 변환해 반환한다.
        
        :param payload: 전송할 데이터 (dict 형태)
        :return: SendTaskResponse 객체
        """
        # SendTaskRequest 모델에 payload를 담아 생성
        request = SendTaskRequest(params=payload)
        # 내부에서 _send_request 메서드를 통해 서버에 POST 요청을 보낸 후, 결과를 반환
        return SendTaskResponse(**await self._send_request(request))

    async def send_task_streaming(
        self, payload: dict[str, Any]
    ) -> AsyncIterable[SendTaskStreamingResponse]:
        """
        SSE(Server-Sent Events) 기반으로 'task'를 스트리밍 전송하는 메서드.
        요청을 보내면 서버가 이벤트 스트림 형태로 응답을 주고, 이를 비동기로 순차적으로 읽어서 전달한다.

        :param payload: 전송할 데이터 (dict 형태)
        :return: SendTaskStreamingResponse 모델을 순차적으로 yield 하는 비동기 이터레이터
        """
        # SendTaskStreamingRequest 모델에 payload를 담아 생성
        request = SendTaskStreamingRequest(params=payload)
        
        # httpx.Client를 사용해 POST 요청 + SSE 연결
        with httpx.Client(timeout=None) as client:
            with connect_sse(
                client, "POST", self.url, json=request.model_dump()
            ) as event_source:
                try:
                    # event_source.iter_sse() 를 이용해 들어오는 SSE 이벤트를 순차 처리
                    for sse in event_source.iter_sse():
                        # SSE로 전송된 data를 받아서 SendTaskStreamingResponse로 변환
                        yield SendTaskStreamingResponse(**json.loads(sse.data))
                except json.JSONDecodeError as e:
                    # JSON 디코딩 오류 발생 시 사용자 정의 예외를 발생
                    raise A2AClientJSONError(str(e)) from e
                except httpx.RequestError as e:
                    # HTTP 요청과 관련된 오류 발생 시 사용자 정의 예외를 발생
                    raise A2AClientHTTPError(400, str(e)) from e

    async def _send_request(self, request: JSONRPCRequest) -> dict[str, Any]:
        """
        실제로 httpx.AsyncClient를 사용해 서버로 POST 요청을 보내는 내부 헬퍼 메서드.
        JSON-RPC 규약에 맞게 request를 직렬화하여 전송한다.

        :param request: JSONRPCRequest 모델 (파라미터, 메서드명 등을 담고 있음)
        :return: dict 형태의 응답 데이터
        """
        async with httpx.AsyncClient() as client:
            try:
                # 30초의 타임아웃을 걸고 POST 요청을 전송
                response = await client.post(
                    self.url, json=request.model_dump(), timeout=30
                )
                # HTTP 에러(4xx, 5xx)가 발생하면 예외를 던짐
                response.raise_for_status()
                # 정상적인 경우 JSON 응답을 dict 형태로 반환
                return response.json()
            except httpx.HTTPStatusError as e:
                # 상태 코드가 4xx/5xx 등의 에러가 있으면 A2AClientHTTPError 예외로 감싸서 발생
                raise A2AClientHTTPError(e.response.status_code, str(e)) from e
            except json.JSONDecodeError as e:
                # JSON 디코딩 오류가 있으면 A2AClientJSONError 예외로 감싸서 발생
                raise A2AClientJSONError(str(e)) from e

    async def get_task(self, payload: dict[str, Any]) -> GetTaskResponse:
        """
        서버로부터 특정 'task' 상태나 결과를 조회(GET)하는 메서드.
        
        :param payload: 조회를 위한 파라미터 (dict 형태)
        :return: GetTaskResponse 객체
        """
        request = GetTaskRequest(params=payload)
        return GetTaskResponse(**await self._send_request(request))

    async def cancel_task(self, payload: dict[str, Any]) -> CancelTaskResponse:
        """
        서버로 요청했던 'task'를 취소하는 메서드.

        :param payload: 취소를 원하는 task 정보를 담은 파라미터 (dict 형태)
        :return: CancelTaskResponse 객체
        """
        request = CancelTaskRequest(params=payload)
        return CancelTaskResponse(**await self._send_request(request))

    async def set_task_callback(
        self, payload: dict[str, Any]
    ) -> SetTaskPushNotificationResponse:
        """
        작업(task) 완료 후에 받을 콜백(푸시 알림 등)을 설정하는 메서드.

        :param payload: 콜백 설정 정보를 담은 파라미터 (dict 형태)
        :return: SetTaskPushNotificationResponse 객체
        """
        request = SetTaskPushNotificationRequest(params=payload)
        return SetTaskPushNotificationResponse(**await self._send_request(request))

    async def get_task_callback(
        self, payload: dict[str, Any]
    ) -> GetTaskPushNotificationResponse:
        """
        이미 설정된 작업의 콜백(푸시 알림) 정보를 조회하는 메서드.

        :param payload: 콜백 정보를 확인할 task 정보를 담은 파라미터 (dict 형태)
        :return: GetTaskPushNotificationResponse 객체
        """
        request = GetTaskPushNotificationRequest(params=payload)
        return GetTaskPushNotificationResponse(**await self._send_request(request))
