import logging

from dao.app_config_dao import app_config_dao

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "campaign_authorized": False,
    "campaign_paused": False,
    "campaign_no_filter": True,
    "campaign_apply_interval_seconds": 10,
    "campaign_batch_interval_seconds": 60,
    "filter_keywords": "",
    "search_url": "",
    "login_success_keyword": "",
}



class UserConfigStore:
    """配置管理类，配置统一持久化到 app_config 表（不再使用 config.json）"""

    def __init__(self):
        self.config = self.load()

    def load(self):
        """从数据库加载配置，数据库为空或不可用时使用默认配置"""
        db_config = app_config_dao.load_config()
        if db_config:
            merged = DEFAULT_CONFIG.copy()
            merged.update(db_config)
            return merged
        return DEFAULT_CONFIG.copy()

    def save(self, config=None):
        """保存配置到数据库（失败时抛出 RuntimeError）"""
        if config is not None:
            self.config = config
        ok = app_config_dao.save_config(self.config)
        if not ok:
            raise RuntimeError("配置保存到数据库失败")
        return True

    def get(self, key, default=None):
        """获取配置项"""
        return self.config.get(key, default)

    def set(self,key,value):
        return self.update({key:value})

    def update(self,updates):
        if not app_config_dao.patch_config(updates):
            raise RuntimeError('配置保存失败')
        self.config=self.load()
        return True

    def load_from_db(self, db=None):
        """从数据库重新加载配置（数据库为准，不合并内存中的旧值）"""
        db_config = app_config_dao.load_config()
        if db_config:
            merged = DEFAULT_CONFIG.copy()
            merged.update(db_config)
            self.config = merged
            return True
        return False


# 全局单例
config = UserConfigStore()


def get_user_config():
    """获取全局配置单例"""
    return config
