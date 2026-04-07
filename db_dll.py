import mysql.connector
import os
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# DB 접속 정보 설정
DB_HOST = os.getenv("MYSQL_HOST", "localhost")
DB_USER = os.getenv("MYSQL_USER")
DB_PASSWORD = os.getenv("MYSQL_PASSWORD")
DB_NAME = os.getenv("MYSQL_DATABASE")

def create_table():
    try:
        # DB 연결
        connection = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME
        )
        cursor = connection.cursor()

        # token_usage_logs 테이블 생성 DDL
        create_table_query = """
        CREATE TABLE IF NOT EXISTS token_usage_logs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            filename VARCHAR(255),
            page_num INT,
            task VARCHAR(50),
            model VARCHAR(100),
            input_tokens INT,
            cached_tokens INT,
            output_tokens INT,
            total_tokens INT,
            input_text TEXT,
            output_text TEXT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """

        cursor.execute(create_table_query)
        connection.commit()
        print("Table 'token_usage_logs' created successfully (or already exists).")

    except Exception as e:
        print(f"Error creating table: {e}")
    finally:
        if 'connection' in locals() and connection.is_connected():
            cursor.close()
            connection.close()

if __name__ == "__main__":
    create_table()
