"""Cloudflare R2 备用上传模块"""
import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

try:
    import boto3
    from botocore.config import Config as BotoConfig
    from botocore.exceptions import ClientError
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False

logger = logging.getLogger(__name__)


class R2Manager:
    """Cloudflare R2 文件管理器"""
    
    def __init__(self, access_key_id: str, secret_access_key: str, bucket_name: str,
                 endpoint: str, public_domain: str, max_total_size_mb: int = 3072, 
                 retention_hours: int = 24):
        """
        初始化 R2 管理器
        
        Args:
            access_key_id: R2 S3 API Token (Access Key ID)
            secret_access_key: R2 S3 API Token (Secret Access Key)
            bucket_name: R2 Bucket 名称
            endpoint: R2 终结点 URL
            public_domain: 公开 URL 域名
            max_total_size_mb: 最大总存储大小（MB）
            retention_hours: 文件保留时间（小时）
        """
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.bucket_name = bucket_name
        self.endpoint = endpoint
        self.public_domain = public_domain.rstrip('/')
        self.max_total_size_bytes = max_total_size_mb * 1024 * 1024
        self.retention_hours = retention_hours
        self._s3_client = None
        self._metadata_file = Path("data/ehentai/.r2_metadata.json")
        self._metadata_file.parent.mkdir(parents=True, exist_ok=True)
        self._is_available = HAS_BOTO3 and bool(access_key_id and secret_access_key and endpoint)
        
    @property
    def is_available(self) -> bool:
        """检查 R2 是否可用"""
        return self._is_available
    
    def _get_s3_client(self):
        """获取或创建 S3 客户端"""
        if self._s3_client is None:
            if not HAS_BOTO3:
                raise ImportError("boto3 not installed. Install it with: pip install boto3")
            
            # 使用 R2 S3 API Token 认证
            self._s3_client = boto3.client(
                "s3",
                endpoint_url=self.endpoint,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
                region_name="auto",
                config=BotoConfig(signature_version="s3v4")
            )
        
        return self._s3_client
    
    async def upload_file(self, file_path: str, file_name: Optional[str] = None) -> Optional[str]:
        """
        上传文件到 R2
        
        Args:
            file_path: 本地文件路径
            file_name: R2 中的文件名（可选）
            
        Returns:
            公开 URL 或 None（失败）
        """
        if not self.is_available:
            logger.warning("[R2] R2 未配置或不可用")
            return None
        
        try:
            file_path_obj = Path(file_path)
            if not file_path_obj.exists():
                logger.error(f"[R2] 文件不存在: {file_path}")
                return None
            
            file_size = file_path_obj.stat().st_size
            file_name = file_name or file_path_obj.name
            
            # 清理过期文件和检查空间
            await self._cleanup_and_check_space(file_size)
            
            # 上传文件
            logger.info(f"[R2] 开始上传文件: {file_name} ({file_size / 1024 / 1024:.2f} MB)")
            
            # 在线程中运行 S3 操作（boto3 是同步的）
            loop = asyncio.get_event_loop()
            public_url = await loop.run_in_executor(
                None, 
                self._upload_file_sync,
                file_path,
                file_name,
                file_size
            )
            
            if public_url:
                logger.info(f"[R2] 上传成功: {public_url}")
                self._update_metadata(file_name, file_size)
                return public_url
            else:
                logger.error("[R2] 上传失败")
                return None
                
        except Exception as error:
            logger.error(f"[R2] 上传异常: {error}", exc_info=True)
            return None
    
    def _upload_file_sync(self, file_path: str, file_name: str, file_size: int) -> Optional[str]:
        """同步上传文件（在 executor 中运行）"""
        try:
            client = self._get_s3_client()
            
            with open(file_path, 'rb') as f:
                client.put_object(
                    Bucket=self.bucket_name,
                    Key=file_name,
                    Body=f,
                    ContentLength=file_size
                )
            
            # 生成公开 URL
            public_url = f"{self.public_domain}/{file_name}"
            return public_url
            
        except ClientError as error:
            logger.error(f"[R2] S3 错误: {error}")
            return None
    
    async def _cleanup_and_check_space(self, required_size: int) -> None:
        """清理过期文件并检查空间"""
        try:
            metadata = self._load_metadata()
            now = datetime.now()
            
            # 1. 删除所有过期文件
            expired_files = []
            for file_name, info in metadata.items():
                upload_time = datetime.fromisoformat(info['upload_time'])
                if now - upload_time > timedelta(hours=self.retention_hours):
                    expired_files.append(file_name)
            
            for file_name in expired_files:
                await self._delete_file(file_name)
                del metadata[file_name]
                logger.info(f"[R2] 删除过期文件: {file_name}")
            
            # 2. 计算当前总大小
            total_size = sum(info['size'] for info in metadata.values())
            
            # 3. 如果加上新文件会超过限制，删除最早的文件
            if total_size + required_size > self.max_total_size_bytes:
                logger.warning(f"[R2] 存储容量不足，需要清理文件 (当前: {total_size / 1024 / 1024:.2f} MB, 需要: {required_size / 1024 / 1024:.2f} MB)")
                
                # 按上传时间排序
                sorted_files = sorted(
                    metadata.items(),
                    key=lambda x: datetime.fromisoformat(x[1]['upload_time'])
                )
                
                # 删除最早的文件，直到有足够空间
                for file_name, info in sorted_files:
                    if total_size + required_size <= self.max_total_size_bytes:
                        break
                    
                    await self._delete_file(file_name)
                    total_size -= info['size']
                    del metadata[file_name]
                    logger.info(f"[R2] 删除最早文件: {file_name} ({info['size'] / 1024 / 1024:.2f} MB)")
            
            # 保存更新后的元数据
            self._save_metadata(metadata)
            
        except Exception as error:
            logger.error(f"[R2] 清理空间异常: {error}", exc_info=True)
    
    async def _delete_file(self, file_name: str) -> bool:
        """删除 R2 中的文件"""
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._delete_file_sync,
                file_name
            )
            return result
        except Exception as error:
            logger.error(f"[R2] 删除文件异常 {file_name}: {error}")
            return False
    
    def _delete_file_sync(self, file_name: str) -> bool:
        """同步删除文件"""
        try:
            client = self._get_s3_client()
            client.delete_object(Bucket=self.bucket_name, Key=file_name)
            return True
        except ClientError as error:
            logger.error(f"[R2] S3 删除错误: {error}")
            return False
    
    def _load_metadata(self) -> dict:
        """加载文件元数据"""
        try:
            if self._metadata_file.exists():
                with open(self._metadata_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as error:
            logger.error(f"[R2] 加载元数据异常: {error}")
        
        return {}
    
    def _save_metadata(self, metadata: dict) -> None:
        """保存文件元数据"""
        try:
            self._metadata_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._metadata_file, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
        except Exception as error:
            logger.error(f"[R2] 保存元数据异常: {error}")
    
    def _update_metadata(self, file_name: str, file_size: int) -> None:
        """更新文件元数据"""
        metadata = self._load_metadata()
        metadata[file_name] = {
            'upload_time': datetime.now().isoformat(),
            'size': file_size
        }
        self._save_metadata(metadata)
    
    async def get_upload_stats(self) -> dict:
        """获取上传统计信息"""
        try:
            metadata = self._load_metadata()
            total_size = sum(info['size'] for info in metadata.values())
            
            now = datetime.now()
            valid_files = []
            expired_count = 0
            
            for file_name, info in metadata.items():
                upload_time = datetime.fromisoformat(info['upload_time'])
                if now - upload_time > timedelta(hours=self.retention_hours):
                    expired_count += 1
                else:
                    valid_files.append((file_name, info))
            
            return {
                'total_files': len(metadata),
                'valid_files': len(valid_files),
                'expired_files': expired_count,
                'total_size_mb': total_size / 1024 / 1024,
                'max_size_mb': self.max_total_size_bytes / 1024 / 1024,
                'usage_percent': (total_size / self.max_total_size_bytes) * 100
            }
        except Exception as error:
            logger.error(f"[R2] 获取统计异常: {error}")
            return {}


# 全局实例
_r2_manager: Optional[R2Manager] = None


async def init_r2_manager(config) -> Optional[R2Manager]:
    """初始化 R2 管理器"""
    global _r2_manager

    if not getattr(config, "ehentai_r2_enabled", False):
        logger.info("[R2] R2 备用上传未启用 (EHENTAI_R2_ENABLED=false)")
        return None
    
    if not config.ehentai_r2_access_key_id or not config.ehentai_r2_secret_access_key or not config.ehentai_r2_endpoint:
        logger.info("[R2] R2 备用上传未配置")
        return None
    
    _r2_manager = R2Manager(
        access_key_id=config.ehentai_r2_access_key_id,
        secret_access_key=config.ehentai_r2_secret_access_key,
        bucket_name=config.ehentai_r2_bucket_name,
        endpoint=config.ehentai_r2_endpoint,
        public_domain=config.ehentai_r2_public_domain,
        max_total_size_mb=config.ehentai_r2_max_total_size_mb,
        retention_hours=config.ehentai_r2_file_retention_hours
    )
    
    if _r2_manager.is_available:
        logger.info("[R2] R2 管理器初始化成功")
    else:
        logger.warning("[R2] R2 管理器初始化失败，R2 功能将不可用")
    
    return _r2_manager


def get_r2_manager() -> Optional[R2Manager]:
    """获取 R2 管理器实例"""
    return _r2_manager
