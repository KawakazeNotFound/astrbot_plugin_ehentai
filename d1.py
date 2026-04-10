import httpx
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

class D1Manager:
    """Cloudflare D1 数据库记录管理器"""
    
    def __init__(self, account_id: str, database_id: str, api_token: str):
        self.account_id = account_id
        self.database_id = database_id
        self.api_token = api_token
        self.base_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database/{database_id}/query"
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json"
        }

    async def _execute(self, sql: str, params: List[Any] = None) -> Dict[str, Any]:
        """执行 SQL 查询"""
        payload = {
            "sql": sql,
            "params": params or []
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(self.base_url, headers=self.headers, json=payload)
            data = resp.json()
            if not data.get("success"):
                logger.error(f"[D1] SQL 执行失败: {data}")
                raise RuntimeError(f"D1 API Error: {data}")
            # D1 返回格式: {"success": true, "result": [{"results": [...]}]}
            result = data.get("result", [{}])[0]
            # 如果包含 "results" 字段，说明是 SELECT 查询，返回结果列表
            if "results" in result:
                return result.get("results", [])
            return result

    async def init_table(self):
        """初始化下载历史表"""
        sql = """
        CREATE TABLE IF NOT EXISTS download_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gid TEXT,
            title TEXT,
            file_size_mb REAL,
            user_id TEXT,
            r2_url TEXT,
            upload_time TEXT,
            last_request_time TEXT,
            expiry_time TEXT,
            request_count INTEGER DEFAULT 1
        );
        """
        try:
            await self._execute(sql)
            logger.info("[D1] 数据库表初始化完成")
            # 尝试迁移旧数据库（添加缺失的列）
            await self._migrate_old_schema()
        except Exception as e:
            logger.error(f"[D1] 初始化表失败: {e}")
    
    async def _migrate_old_schema(self):
        """迁移旧数据库架构（添加缺失的列）"""
        try:
            # 尝试添加 last_request_time 列
            try:
                add_last_request_time = "ALTER TABLE download_history ADD COLUMN last_request_time TEXT;"
                await self._execute(add_last_request_time)
                logger.info("[D1] 成功添加 last_request_time 列")
            except Exception as e:
                # 列可能已存在，忽略错误
                if "duplicate column" not in str(e).lower():
                    logger.debug(f"[D1] last_request_time 列: {e}")
            
            # 尝试添加 request_count 列
            try:
                add_request_count = "ALTER TABLE download_history ADD COLUMN request_count INTEGER DEFAULT 1;"
                await self._execute(add_request_count)
                logger.info("[D1] 成功添加 request_count 列")
            except Exception as e:
                # 列可能已存在，忽略错误
                if "duplicate column" not in str(e).lower():
                    logger.debug(f"[D1] request_count 列: {e}")
            
            # 为旧数据初始化 last_request_time（使用 upload_time）
            update_migrate = """
            UPDATE download_history 
            SET last_request_time = COALESCE(last_request_time, upload_time)
            WHERE last_request_time IS NULL;
            """
            try:
                await self._execute(update_migrate)
                logger.info("[D1] 数据库迁移完成，旧数据已初始化")
            except Exception as e:
                logger.debug(f"[D1] 数据初始化: {e}")
                
        except Exception as e:
            logger.warning(f"[D1] 数据库迁移失败（可能不需要）: {e}")

    async def record_download(self, gid: str, title: str, size_mb: float, user_id: str, r2_url: str, retention_hours: int):
        """记录一条下载历史（同用户同本子则更新计数，否则新增）"""
        now = datetime.now()
        expiry = now + timedelta(hours=retention_hours)
        
        # 先查询是否存在相同的 (gid, user_id) 记录
        check_sql = "SELECT id, request_count FROM download_history WHERE gid = ? AND user_id = ?;"
        try:
            results = await self._execute(check_sql, [gid, str(user_id)])
            
            # 如果 results 是列表且不为空，说明找到了记录
            if isinstance(results, list) and len(results) > 0:
                # 更新现有记录
                update_sql = """
                UPDATE download_history 
                SET request_count = request_count + 1, 
                    last_request_time = ?,
                    expiry_time = ?
                WHERE gid = ? AND user_id = ?;
                """
                await self._execute(update_sql, [now.isoformat(), expiry.isoformat(), gid, str(user_id)])
                logger.info(f"[D1] 更新下载记录: gid={gid}, user_id={user_id}, 请求次数已增加")
            else:
                # 新增记录
                insert_sql = """
                INSERT INTO download_history (gid, title, file_size_mb, user_id, r2_url, upload_time, last_request_time, expiry_time, request_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1);
                """
                params = [
                    gid, 
                    title, 
                    round(size_mb, 2), 
                    str(user_id), 
                    r2_url, 
                    now.isoformat(),
                    now.isoformat(), 
                    expiry.isoformat()
                ]
                await self._execute(insert_sql, params)
                logger.info(f"[D1] 成功记录新下载历史: {title} (user_id={user_id})")
        except Exception as e:
            logger.error(f"[D1] 记录下载历史失败: {e}")

    async def cleanup_expired_metadata(self) -> int:
        """从 D1 中清理过期的记录（注意：这不删除 R2 文件，只删除数据库元数据）"""
        now = datetime.now().isoformat()
        sql = "DELETE FROM download_history WHERE expiry_time < ?;"
        try:
            res = await self._execute(sql, [now])
            # D1 query API 返回结果因版本而异，这里简单返回
            return 1
        except Exception as e:
            logger.error(f"[D1] 清理过期记录失败: {e}")
            return 0

_d1_manager: Optional[D1Manager] = None

async def init_d1_manager(config) -> Optional[D1Manager]:
    global _d1_manager
    if not getattr(config, "ehentai_d1_enabled", False):
        return None
    
    account_id = config.ehentai_d1_account_id
    # 如果没配 Account ID，从 R2 Endpoint 提取 (https://ACCOUNT_ID.r2.cloudflarestorage.com)
    if not account_id and config.ehentai_r2_endpoint:
        try:
            account_id = config.ehentai_r2_endpoint.split("//")[1].split(".")[0]
            logger.info(f"[D1] 从 R2 Endpoint 自动提取 Account ID: {account_id}")
        except Exception:
            pass
            
    if not account_id or not config.ehentai_d1_database_id or not config.ehentai_d1_api_token:
        logger.warning("[D1] D1 数据库配置不完整，记录功能已禁用")
        return None
        
    _d1_manager = D1Manager(account_id, config.ehentai_d1_database_id, config.ehentai_d1_api_token)
    await _d1_manager.init_table()
    return _d1_manager

def get_d1_manager() -> Optional[D1Manager]:
    return _d1_manager
