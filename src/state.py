"""지난 주기의 결과를 기억하는 자리. Lambda 는 실행마다 백지라 바깥(DynamoDB)에 두고, 로컬 · 테스트는 파일 · 메모리로 같은 모양을 쓴다 (#26)."""

import json
import os

# 표 하나에 항목 하나. 여러 Watchdog 이 생기면 그때 이름을 가른다
KEY = "watchdog"


class MemoryStateStore:
    """테스트용. 프로세스 안에서만 기억한다"""

    def __init__(self, state=None):
        self.state = state

    def load(self):
        return self.state

    def save(self, state) -> None:
        self.state = state


class FileStateStore:
    """로컬 실행용. 파일이 없으면 기억 없음"""

    def __init__(self, path: str):
        self.path = path

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return None

    def save(self, state) -> None:
        # 쓰다 죽어도 반쪽 파일이 남지 않게 옆에 썼다가 바꿔 끼운다
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, self.path)


class DynamoStateStore:
    """운영용. 상태를 JSON 문자열 한 칸에 넣는다 — 숫자를 Decimal 로 바꾸는 DynamoDB 변환을 피하려고"""

    def __init__(self, table_name: str):
        import boto3  # Lambda 파이썬 런타임에 들어 있다. 로컬 · 테스트는 이 클래스를 안 써서 설치하지 않는다

        self.table = boto3.resource("dynamodb").Table(table_name)

    def load(self):
        item = self.table.get_item(Key={"pk": KEY}).get("Item")
        return json.loads(item["state"]) if item else None

    def save(self, state) -> None:
        self.table.put_item(Item={"pk": KEY, "state": json.dumps(state, ensure_ascii=False)})


def from_env(env=os.environ):
    """STATE_TABLE 이 있으면 DynamoDB, 아니면 STATE_FILE, 둘 다 없으면 None (기억 없이 돈다 = 매 주기 현재 문제를 다시 알린다)"""
    table = env.get("STATE_TABLE")
    if table:
        return DynamoStateStore(table)
    path = env.get("STATE_FILE")
    if path:
        return FileStateStore(path)
    return None
