from typing import Callable # 함수 타입
import uuid
from common.types import (
    AgentCard,
    Task,
    TaskSendParams,
    TaskStatusUpdateEvent,
    TaskArtifactUpdateEvent,
    TaskStatus,
    TaskState,
)
from common.client import A2AClient

TaskCallbackArg = Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent
TaskUpdateCallback = Callable[[TaskCallbackArg], Task] # 타입 별칭

class RemoteAgentConnections:
  """A class to hold the connections to the remote agents.""" # 원격 에이전트들과의 연결을 저장하는 클래스

  def __init__(self, agent_card: AgentCard): # 외부 Agent를 AgentCard 타입으로 가져옴
    self.agent_client = A2AClient(agent_card) # 외부 Agent A2A Client Init
    self.card = agent_card # agent_card 정보 저장

    self.conversation_name = None
    self.conversation = None
    self.pending_tasks = set()

  def get_agent(self) -> AgentCard: # 외부 Agent 정보 리턴
    return self.card

  async def send_task(
      self,
      request: TaskSendParams,
      task_callback: TaskUpdateCallback | None,
  ) -> Task | None:
    if self.card.capabilities.streaming:
      task = None
      if task_callback:
        task_callback(Task(
            id=request.id,
            sessionId=request.sessionId,
            status=TaskStatus(
                state=TaskState.SUBMITTED,
                message=request.message,
            ),
            history=[request.message],
        ))
      async for response in self.agent_client.send_task_streaming(request.model_dump()):
        merge_metadata(response.result, request)
        # For task status updates, we need to propagate metadata and provide
        # a unique message id.
        if (hasattr(response.result, 'status') and
            hasattr(response.result.status, 'message') and
            response.result.status.message):
          merge_metadata(response.result.status.message, request.message)
          m = response.result.status.message
          if not m.metadata:
            m.metadata = {}
          if 'message_id' in m.metadata:
            m.metadata['last_message_id'] = m.metadata['message_id']
          m.metadata['message_id'] = str(uuid.uuid4())
        if task_callback:
          task = task_callback(response.result)
        if hasattr(response.result, 'final') and response.result.final:
          break
      return task
    else: # Non-streaming
      response = await self.agent_client.send_task(request.model_dump())
      merge_metadata(response.result, request)
      # For task status updates, we need to propagate metadata and provide
      # a unique message id.
      if (hasattr(response.result, 'status') and
          hasattr(response.result.status, 'message') and
          response.result.status.message):
        merge_metadata(response.result.status.message, request.message)
        m = response.result.status.message
        if not m.metadata:
          m.metadata = {}
        if 'message_id' in m.metadata:
          m.metadata['last_message_id'] = m.metadata['message_id']
        m.metadata['message_id'] = str(uuid.uuid4())

      if task_callback:
        task_callback(response.result)
      return response.result

def merge_metadata(target, source):
  if not hasattr(target, 'metadata') or not hasattr(source, 'metadata'):
    return
  if target.metadata and source.metadata:
    target.metadata.update(source.metadata)
  elif source.metadata:
    target.metadata = dict(**source.metadata)

