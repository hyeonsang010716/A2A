# -------------------- 표준 라이브러리 / 타입 임포트 --------------------
from abc import ABC, abstractmethod                    # 추상 클래스 정의용
from typing import Union, AsyncIterable, List
import asyncio                                         # 비동기 동시성
import logging

# -------------------- 프로젝트 공통 타입 --------------------
from common.types import Task                          # 실제 작업(채팅 세션 등) 모델
from common.types import (
    # JSON‑RPC 응답 / 에러 / 각종 요청 모델
    JSONRPCResponse,
    TaskIdParams, TaskQueryParams, GetTaskRequest,
    TaskNotFoundError, SendTaskRequest, CancelTaskRequest,
    TaskNotCancelableError, SetTaskPushNotificationRequest,
    GetTaskPushNotificationRequest, GetTaskResponse,
    CancelTaskResponse, SendTaskResponse, SetTaskPushNotificationResponse,
    GetTaskPushNotificationResponse, PushNotificationNotSupportedError,
    TaskSendParams, TaskStatus, TaskState, TaskResubscriptionRequest,
    SendTaskStreamingRequest, SendTaskStreamingResponse, Artifact,
    PushNotificationConfig, TaskStatusUpdateEvent, JSONRPCError,
    TaskPushNotificationConfig, InternalError,
)
from common.server.utils import new_not_implemented_error

logger = logging.getLogger(__name__)


# ============================================================================
# 1) 추상 TaskManager ─ A2A 서버가 의존하는 “작업 처리” 인터페이스
# ============================================================================

class TaskManager(ABC):
    """실제 비즈니스 로직을 구현하기 위한 인터페이스(추상 클래스)."""

    @abstractmethod
    async def on_get_task(self, request: GetTaskRequest) -> GetTaskResponse:
        """작업 조회(GET)"""
        pass

    @abstractmethod
    async def on_cancel_task(self, request: CancelTaskRequest) -> CancelTaskResponse:
        """작업 취소(CANCEL)"""
        pass

    @abstractmethod
    async def on_send_task(self, request: SendTaskRequest) -> SendTaskResponse:
        """작업 생성·전송(SEND)"""
        pass

    @abstractmethod
    async def on_send_task_subscribe(
        self, request: SendTaskStreamingRequest
    ) -> Union[AsyncIterable[SendTaskStreamingResponse], JSONRPCResponse]:
        """작업 생성 + SSE 스트리밍 응답"""
        pass

    @abstractmethod
    async def on_set_task_push_notification(
        self, request: SetTaskPushNotificationRequest
    ) -> SetTaskPushNotificationResponse:
        """푸시 알림 설정"""
        pass

    @abstractmethod
    async def on_get_task_push_notification(
        self, request: GetTaskPushNotificationRequest
    ) -> GetTaskPushNotificationResponse:
        """푸시 알림 조회"""
        pass

    @abstractmethod
    async def on_resubscribe_to_task(
        self, request: TaskResubscriptionRequest
    ) -> Union[AsyncIterable[SendTaskStreamingResponse], JSONRPCResponse]:
        """끊어진 SSE 스트림 재구독"""
        pass


# ============================================================================
# 2) InMemoryTaskManager ─ 메모리 딕셔너리 기반 간단 구현
# ============================================================================

class InMemoryTaskManager(TaskManager):
    """DB 없이 메모리에만 저장하는 데모/테스트용 TaskManager."""

    def __init__(self):
        self.tasks: dict[str, Task] = {}                            # task_id → Task
        self.push_notification_infos: dict[str, PushNotificationConfig] = {}
        self.lock = asyncio.Lock()                                  # tasks 접근 보호
        # SSE 구독자 관리: task_id → [asyncio.Queue, ...]
        self.task_sse_subscribers: dict[str, List[asyncio.Queue]] = {}
        self.subscriber_lock = asyncio.Lock()                       # 구독자 목록 보호

    # ------------------------------------------------------------------
    # GET /tasks
    # ------------------------------------------------------------------
    async def on_get_task(self, request: GetTaskRequest) -> GetTaskResponse:
        logger.info(f"Getting task {request.params.id}")
        task_query_params: TaskQueryParams = request.params

        async with self.lock:
            task = self.tasks.get(task_query_params.id)
            if task is None:
                return GetTaskResponse(id=request.id, error=TaskNotFoundError())

            # historyLength 옵션에 따라 히스토리를 잘라서 반환
            task_result = self.append_task_history(
                task, task_query_params.historyLength
            )

        return GetTaskResponse(id=request.id, result=task_result)

    # ------------------------------------------------------------------
    # CANCEL /tasks/{id}
    # ------------------------------------------------------------------
    async def on_cancel_task(self, request: CancelTaskRequest) -> CancelTaskResponse:
        logger.info(f"Cancelling task {request.params.id}")
        task_id_params: TaskIdParams = request.params

        async with self.lock:
            task = self.tasks.get(task_id_params.id)
            if task is None:
                return CancelTaskResponse(id=request.id, error=TaskNotFoundError())

        # 실제 취소 로직은 미구현 → 항상 NotCancelable 에러 반환
        return CancelTaskResponse(id=request.id, error=TaskNotCancelableError())

    # ------------------------------------------------------------------
    # SEND /tasks (구현은 서브클래스 책임)
    # ------------------------------------------------------------------
    @abstractmethod
    async def on_send_task(self, request: SendTaskRequest) -> SendTaskResponse:
        pass

    # SEND + Subscribe (SSE)
    @abstractmethod
    async def on_send_task_subscribe(
        self, request: SendTaskStreamingRequest
    ) -> Union[AsyncIterable[SendTaskStreamingResponse], JSONRPCResponse]:
        pass

    # ------------------------------------------------------------------
    # Push Notification 설정/조회
    # ------------------------------------------------------------------
    async def set_push_notification_info(self, task_id: str, notification_config: PushNotificationConfig):
        """딕셔너리에 저장(없으면 에러)"""
        async with self.lock:
            task = self.tasks.get(task_id)
            if task is None:
                raise ValueError(f"Task not found for {task_id}")
            self.push_notification_infos[task_id] = notification_config

    async def get_push_notification_info(self, task_id: str) -> PushNotificationConfig:
        async with self.lock:
            task = self.tasks.get(task_id)
            if task is None:
                raise ValueError(f"Task not found for {task_id}")
            return self.push_notification_infos[task_id]

    async def has_push_notification_info(self, task_id: str) -> bool:
        async with self.lock:
            return task_id in self.push_notification_infos

    async def on_set_task_push_notification(
        self, request: SetTaskPushNotificationRequest
    ) -> SetTaskPushNotificationResponse:
        logger.info(f"Setting task push notification {request.params.id}")
        params: TaskPushNotificationConfig = request.params

        try:
            await self.set_push_notification_info(params.id, params.pushNotificationConfig)
        except Exception as e:
            logger.error(f"Error while setting push notification info: {e}")
            return JSONRPCResponse(
                id=request.id,
                error=InternalError(message="Failed to set push notification info"),
            )

        return SetTaskPushNotificationResponse(id=request.id, result=params)

    async def on_get_task_push_notification(
        self, request: GetTaskPushNotificationRequest
    ) -> GetTaskPushNotificationResponse:
        logger.info(f"Getting task push notification {request.params.id}")
        task_params: TaskIdParams = request.params

        try:
            info = await self.get_push_notification_info(task_params.id)
        except Exception as e:
            logger.error(f"Error while getting push notification info: {e}")
            return GetTaskPushNotificationResponse(
                id=request.id,
                error=InternalError(message="Failed to get push notification info"),
            )

        result = TaskPushNotificationConfig(id=task_params.id, pushNotificationConfig=info)
        return GetTaskPushNotificationResponse(id=request.id, result=result)

    # ------------------------------------------------------------------
    # 내부 헬퍼: task 저장/업데이트
    # ------------------------------------------------------------------
    async def upsert_task(self, task_send_params: TaskSendParams) -> Task:
        """
        • 새 task_id면 Task 생성  
        • 기존 task면 history만 추가  
        (실제 모델 실행은 구현체가 담당)
        """
        logger.info(f"Upserting task {task_send_params.id}")
        async with self.lock:
            task = self.tasks.get(task_send_params.id)
            if task is None:
                task = Task(
                    id=task_send_params.id,
                    sessionId=task_send_params.sessionId,
                    messages=[task_send_params.message],
                    status=TaskStatus(state=TaskState.SUBMITTED),
                    history=[task_send_params.message],
                )
                self.tasks[task_send_params.id] = task
            else:
                task.history.append(task_send_params.message)

            return task

    # ------------------------------------------------------------------
    # (옵션) 재구독 기능은 아직 미구현
    # ------------------------------------------------------------------
    async def on_resubscribe_to_task(
        self, request: TaskResubscriptionRequest
    ) -> Union[AsyncIterable[SendTaskStreamingResponse], JSONRPCResponse]:
        return new_not_implemented_error(request.id)

    # ------------------------------------------------------------------
    # 작업 상태/아티팩트 업데이트
    # ------------------------------------------------------------------
    async def update_store(
        self, task_id: str, status: TaskStatus, artifacts: list[Artifact]
    ) -> Task:
        """모델 추론이 진행되며 상태·아티팩트를 갱신할 때 사용."""
        async with self.lock:
            try:
                task = self.tasks[task_id]
            except KeyError:
                logger.error(f"Task {task_id} not found for updating")
                raise ValueError(f"Task {task_id} not found")

            task.status = status

            if status.message is not None:
                task.history.append(status.message)

            if artifacts:
                task.artifacts = (task.artifacts or []) + artifacts

            return task

    # ------------------------------------------------------------------
    # 히스토리 잘라내기 (historyLength 지원)
    # ------------------------------------------------------------------
    def append_task_history(self, task: Task, historyLength: int | None):
        new_task = task.model_copy()
        if historyLength and historyLength > 0:
            new_task.history = new_task.history[-historyLength:]
        else:
            new_task.history = []
        return new_task

    # ------------------------------------------------------------------
    # -------------------- SSE(실시간 스트리밍) 유틸 --------------------
    # ------------------------------------------------------------------
    async def setup_sse_consumer(self, task_id: str, is_resubscribe: bool = False):
        """
        • 새 스트림 구독자를 task_sse_subscribers에 등록  
        • 각 구독자는 asyncio.Queue 로 이벤트를 받음
        """
        async with self.subscriber_lock:
            if task_id not in self.task_sse_subscribers:
                if is_resubscribe:
                    raise ValueError("Task not found for resubscription")
                self.task_sse_subscribers[task_id] = []

            queue = asyncio.Queue(maxsize=0)  # 무제한
            self.task_sse_subscribers[task_id].append(queue)
            return queue

    async def enqueue_events_for_sse(self, task_id: str, task_update_event):
        """모든 구독자 큐에 이벤트 broadcast"""
        async with self.subscriber_lock:
            if task_id not in self.task_sse_subscribers:
                return
            for q in self.task_sse_subscribers[task_id]:
                await q.put(task_update_event)

    async def dequeue_events_for_sse(
        self, request_id, task_id, sse_event_queue: asyncio.Queue
    ) -> AsyncIterable[SendTaskStreamingResponse] | JSONRPCResponse:
        """
        큐에서 이벤트를 꺼내 클라이언트로 스트림 전송.  
        • JSONRPCError   → error 필드로 래핑  
        • TaskStatusUpdateEvent(final=True) → 스트림 종료
        """
        try:
            while True:
                event = await sse_event_queue.get()

                # 에러 발생 시
                if isinstance(event, JSONRPCError):
                    yield SendTaskStreamingResponse(id=request_id, error=event)
                    break

                # 정상 이벤트
                yield SendTaskStreamingResponse(id=request_id, result=event)

                # 최종 상태면 루프 탈출
                if isinstance(event, TaskStatusUpdateEvent) and event.final:
                    break
        finally:
            # 스트림이 끝나면 구독자 목록에서 제거
            async with self.subscriber_lock:
                if task_id in self.task_sse_subscribers:
                    self.task_sse_subscribers[task_id].remove(sse_event_queue)
