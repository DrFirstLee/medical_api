# Swift Medical API (FastAPI + MySQL + Docker)

비즈니스와 의료 환경에서의 실시간 언어 번역 상담 서비스를 위한 백엔드 API 서버 프로젝트입니다. Docker Compose를 통해 FastAPI 서버, MySQL 데이터베이스, 그리고 외부 접속을 위한 Cloudflare Tunnel을 통합하여 제공합니다.

## 🚀 아키텍처 개요

이 프로젝트는 Docker를 기반으로 총 3개의 서비스로 구성되어 있습니다.

1.  **FastAPI Server (App)**: 비즈니스 로직 및 API 엔드포인트 제공.
2.  **MySQL Database**: 상담 세션 및 관련 데이터를 영구 저장. (외부 접근이 차단된 전용 데이터베이스)
3.  **Cloudflare Tunnel**: `trycloudflare.com`을 통해 로컬 서버를 외부 인터넷으로 안전하게 노출.

---

## 🛠 실행 방법

아래 명령어를 사용하여 모든 서비스를 한 번에 빌드하고 실행할 수 있습니다.

```powershell
docker-compose up --build -d
```

---

## 🔗 외부 접속 주소 확인

Cloudflare Tunnel이 생성한 임시 공개 URL은 다음 명령어로 확인할 수 있습니다.

```powershell
docker logs tunnel_server
```

fastapi 로그보기

```bash
 docker logs -f swift_fastapi_server
```

fastapi 서버에들어가기
```
 docker exec -it fastapi_server /bin/bash
```
로그 내용 중 아래와 같은 형식을 찾으세요:
```text
INF |  Your quick Tunnel has been created! Visit it at:
INF |  https://your-random-name.trycloudflare.com
```

---

## 🔒 보안 및 데이터 지속성

### 1. 보안 (Security)
*   **MySQL 차단**: MySQL은 `ports`를 개방하지 않았으므로 오직 FastAPI 서버 내부에서만 접속 가능합니다. 외부(내 컴퓨터 호스트)에서의 직접적인 접근을 차단하여 보안을 높였습니다.
*   **Tunneling**: 복잡한 포트포워딩 없이 Cloudflare Tunnel을 통해 안전한 HTTPS 연결을 제공합니다.

### 2. 데이터 보존 (Persistence)
*   프로젝트 폴더 내의 `./db_data` 디렉토리가 MySQL 컨테이너 내부(`/var/lib/mysql`)와 연결되어 있습니다. 도커를 껐다 켜거나 컨테이너를 삭제해도 데이터가 로컬 폴더에 파일 형태로 안전하게 유지됩니다.

---

## 🧪 연결 테스트

API 서버가 데이터베이스와 성공적으로 통신하는지 확인하려면 다음 엔드포인트를 호출하세요.

*   **URL**: `https://<YOUR-TUNNEL-URL>/db-test`
*   **기능**: FastAPI -> MySQL 연결 시도 후 상태 반환

---

## 📁 주요 파일 구조

*   `app.py`: FastAPI 애플리케이션 코드 및 엔드포인트 정의
*   `docker-compose.yaml`: 서비스 간 네트워크 및 볼륨 오케스트레이션
*   `Dockerfile`: API 서버 빌드 명세서
*   `.env`: DB 비밀번호 및 Open API 키 등 민감 정보 저장
*   `db_data/`: (자동 생성) MySQL 데이터베이스 파일 저장 폴더

---

## 📦 데이터 마이그레이션 (데이터 옮기기)

프로젝트 위치를 변경하거나 동기화할 때 데이터베이스 상태를 그대로 유지하려면 다음 절차를 따르세요.

1.  **컨테이너 중지**: 데이터 일관성을 위해 반드시 실행 중인 컨테이너를 먼저 멈춰야 합니다.
    ```powershell
    docker-compose down
    ```
2.  **데이터 폴더 복사**: 현재 폴더에 있는 `db_data` 디렉토리 전체를 새로운 위치로 복사하거나 압축하여 이동시킵니다.
    *   **주의**: `.gitignore`에 등록되어 있어 Git을 통해서는 옮겨지지 않습니다. 수동으로 복사해야 합니다.
3.  **새 위치에서 실행**: `docker-compose.yaml` 파일과 `db_data` 폴더가 같은 위치에 있게 한 뒤 명령어를 실행합니다.
    ```powershell
    docker-compose up -d
    ```

> [!IMPORTANT]
> MySQL 서버가 **실행 중**일 때 `db_data` 폴더를 복사하면 데이터가 오염될 수 있습니다. 반드시 서버를 중지한 후 복사하세요.