import os
import datetime
import asyncio
import mysql.connector
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# DB 접속 정보 (app.py와 동일한 환경 변수 사용)
DB_HOST = os.getenv("MYSQL_HOST", "db")
DB_PORT = int(os.getenv("MYSQL_PORT", "3306"))
DB_USER = os.getenv("MYSQL_USER")
DB_PASSWORD = os.getenv("MYSQL_PASSWORD")
DB_NAME = os.getenv("MYSQL_DATABASE")

def db_log_token_usage(usage_dict, model, filename="N/A", page_num=0, task="unknown",
                       input_text=None, output_text=None):
    """
    GPT 혹은 Whisper 응답의 토큰 사용량을 MySQL DB에 기록합니다. (동기 버전)
    
    Args:
        usage_dict (dict): OpenAI 응답의 'usage' 데이터
        model (str): 사용된 모델명
        filename (str): 파일 이름
        page_num (int): 페이지 번호
        task (str): 수행한 작업 (stt, translate 등)
        input_text (str, optional): 입력 텍스트 (예: 원본 발화)
        output_text (str, optional): 출력 텍스트 (예: 번역 결과, STT 결과)
    """
    if not usage_dict:
        return

    try:
        connection = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME
        )
        cursor = connection.cursor()

        input_tokens = usage_dict.get("prompt_tokens", 0)
        output_tokens = usage_dict.get("completion_tokens", 0)
        total_tokens = usage_dict.get("total_tokens", 0)
        
        # 캐시된 토큰 추출 (상세 정보가 있는 경우)
        prompt_details = usage_dict.get("prompt_tokens_details", {})
        cached_tokens = prompt_details.get("cached_tokens", 0) if prompt_details else 0

        insert_query = """
        INSERT INTO token_usage_logs 
        (filename, page_num, task, model, input_tokens, cached_tokens, output_tokens, total_tokens, input_text, output_text)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(insert_query, (
            filename, page_num, task, model, 
            input_tokens, cached_tokens, output_tokens, total_tokens,
            input_text, output_text
        ))
        connection.commit()
        cursor.close()
        connection.close()
    except Exception as e:
        print(f"Failed to log token usage to DB in func.py: {e}")


async def db_log_token_usage_async(usage_dict, model, filename="N/A", page_num=0, task="unknown",
                                   input_text=None, output_text=None):
    """
    비동기 래퍼: 동기 DB 로깅을 별도 스레드에서 실행하여 이벤트 루프 블로킹 방지.
    동시에 여러 프론트엔드 요청이 와도 서로 블로킹하지 않습니다.
    """
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,  # 기본 ThreadPoolExecutor 사용
        lambda: db_log_token_usage(usage_dict, model, filename=filename,
                                   page_num=page_num, task=task,
                                   input_text=input_text, output_text=output_text)
    )

