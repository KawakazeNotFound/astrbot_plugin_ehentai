"""AstrBot 插件配置管理模块"""
from typing import Any, Dict, Optional


class PluginConfig:
    """插件配置容器"""
    
    def __init__(self, config_dict: Dict[str, Any]):
        self._config = config_dict
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值"""
        return self._config.get(key, default)
    
    def __getattr__(self, name: str) -> Any:
        """使用属性访问配置值"""
        if name.startswith('_'):
            return object.__getattribute__(self, name)
        
        # 处理嵌套对象
        if name == 'r2_config':
            return self._get_sub_config(['ehentai_r2_access_key_id', 'ehentai_r2_secret_access_key', 
                                        'ehentai_r2_bucket_name', 'ehentai_r2_endpoint', 
                                        'ehentai_r2_public_domain', 'ehentai_r2_max_total_size_mb',
                                        'ehentai_r2_file_retention_hours', 'ehentai_r2_enabled'])
        elif name == 'd1_config':
            return self._get_sub_config(['ehentai_d1_enabled', 'ehentai_d1_account_id',
                                        'ehentai_d1_database_id', 'ehentai_d1_api_token'])
        elif name == 'cleanup_config':
            return self._get_sub_config(['ehentai_auto_cleanup_local', 'ehentai_auto_cleanup_time'])
        
        # 直接获取值
        value = self._config.get(name)
        if value is None:
            # 尝试带 ehentai_ 前缀
            if not name.startswith('ehentai_'):
                value = self._config.get(f'ehentai_{name}')
        return value
    
    def _get_sub_config(self, keys: list) -> Dict[str, Any]:
        """获取子配置对象"""
        sub = {}
        for key in keys:
            sub[key] = self._config.get(key)
        return sub
    
    # 属性代理，使用驼峰式访问
    @property
    def ehentai_site(self) -> str:
        return self.get('ehentai_site', 'e')
    
    @property
    def ehentai_base_url(self) -> str:
        return self.get('ehentai_base_url', 'https://e-hentai.org')
    
    @property
    def ehentai_cookie(self) -> str:
        return self.get('ehentai_cookie', '')
    
    @property
    def ehentai_ipb_member_id(self) -> str:
        return self.get('ehentai_ipb_member_id', '')
    
    @property
    def ehentai_ipb_pass_hash(self) -> str:
        return self.get('ehentai_ipb_pass_hash', '')
    
    @property
    def ehentai_igneous(self) -> str:
        return self.get('ehentai_igneous', '')
    
    @property
    def ehentai_cf_clearance(self) -> str:
        return self.get('ehentai_cf_clearance', '')
    
    @property
    def ehentai_timeout(self) -> int:
        return self.get('ehentai_timeout', 20)
    
    @property
    def ehentai_max_results(self) -> int:
        return self.get('ehentai_max_results', 5)
    
    @property
    def ehentai_download_dir(self) -> str:
        return self.get('ehentai_download_dir', 'data/ehentai')
    
    @property
    def ehentai_proxy(self) -> str:
        return self.get('ehentai_proxy', '')
    
    @property
    def ehentai_http_backend(self) -> str:
        return self.get('ehentai_http_backend', 'httpx')
    
    @property
    def ehentai_http3(self) -> bool:
        return self.get('ehentai_http3', True)
    
    @property
    def ehentai_desktop_site(self) -> bool:
        return self.get('ehentai_desktop_site', False)
    
    @property
    def ehentai_impersonate(self) -> str:
        return self.get('ehentai_impersonate', 'chrome124')
    
    @property
    def ehentai_enable_direct_ip(self) -> bool:
        return self.get('ehentai_enable_direct_ip', True)
    
    @property
    def ehentai_curl_cffi_skip_on_error(self) -> bool:
        return self.get('ehentai_curl_cffi_skip_on_error', True)
    
    @property
    def ehentai_stream_upload_first(self) -> bool:
        return self.get('ehentai_stream_upload_first', True)
    
    @property
    def ehentai_stream_chunk_size(self) -> int:
        return self.get('ehentai_stream_chunk_size', 256 * 1024)
    
    @property
    def ehentai_upload_to_group_file(self) -> bool:
        return self.get('ehentai_upload_to_group_file', False)
    
    @property
    def ehentai_prefer_r2_over_group_file(self) -> bool:
        return self.get('ehentai_prefer_r2_over_group_file', True)
    
    @property
    def ehentai_search_f_cats(self) -> int:
        return self.get('ehentai_search_f_cats', 0)
    
    @property
    def ehentai_search_advsearch(self) -> bool:
        return self.get('ehentai_search_advsearch', False)
    
    @property
    def ehentai_r2_enabled(self) -> bool:
        return self.get('ehentai_r2_enabled', False)
    
    @property
    def ehentai_r2_access_key_id(self) -> str:
        return self.get('ehentai_r2_access_key_id', '')
    
    @property
    def ehentai_r2_secret_access_key(self) -> str:
        return self.get('ehentai_r2_secret_access_key', '')
    
    @property
    def ehentai_r2_bucket_name(self) -> str:
        return self.get('ehentai_r2_bucket_name', 'ehentai')
    
    @property
    def ehentai_r2_endpoint(self) -> str:
        return self.get('ehentai_r2_endpoint', '')
    
    @property
    def ehentai_r2_public_domain(self) -> str:
        return self.get('ehentai_r2_public_domain', 'https://botgeneratedcontent.0061226.xyz')
    
    @property
    def ehentai_r2_max_total_size_mb(self) -> int:
        return self.get('ehentai_r2_max_total_size_mb', 3072)
    
    @property
    def ehentai_r2_file_retention_hours(self) -> int:
        return self.get('ehentai_r2_file_retention_hours', 24)
    
    @property
    def ehentai_d1_enabled(self) -> bool:
        return self.get('ehentai_d1_enabled', False)
    
    @property
    def ehentai_d1_account_id(self) -> str:
        return self.get('ehentai_d1_account_id', '')
    
    @property
    def ehentai_d1_database_id(self) -> str:
        return self.get('ehentai_d1_database_id', '')
    
    @property
    def ehentai_d1_api_token(self) -> str:
        return self.get('ehentai_d1_api_token', '')
    
    @property
    def ehentai_auto_cleanup_local(self) -> bool:
        return self.get('ehentai_auto_cleanup_local', True)
    
    @property
    def ehentai_auto_cleanup_time(self) -> str:
        return self.get('ehentai_auto_cleanup_time', '03:00')
