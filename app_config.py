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
    "deepseek_enabled": False,
    "ollama_enabled": False,
    "ai_provider": "deepseek",
}



class AppConfig:
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

    def render_prompt(self, template, variables):
        """渲染提示词模板，支持 {占位符} 引用配置项

        Args:
            template: 提示词模板字符串
            variables: 变量字典，如 {'job_title': 'xxx', 'skills': 'yyy'}
        """
        result = template
        for key, value in variables.items():
            placeholder = "{" + key + "}"
            if placeholder in result:
                result = result.replace(placeholder, str(value) if value else '')
        return result

    # ===== 兼容旧接口（参数 db 保留兼容，实际始终以数据库为准） =====
    def save_to_db(self, db=None):
        """保存配置到数据库"""
        return self.save()

    def load_from_db(self, db=None):
        """从数据库重新加载配置（数据库为准，不合并内存中的旧值）"""
        db_config = app_config_dao.load_config()
        if db_config:
            merged = DEFAULT_CONFIG.copy()
            merged.update(db_config)
            self.config = merged
            return True
        return False

    def render_with_job(self, template, job_info):
        """根据职位信息渲染提示词模板

        支持占位符：
          {job_title} {job_description} {job_requirements} {salary}
          {location} {experience} {education} {company}
          {salary_range} {skills} {greeting}
        """
        variables = {
            'job_title': job_info.get('title', ''),
            'job_description': job_info.get('description', ''),
            'job_requirements': job_info.get('requirements', ''),
            'salary': job_info.get('salary', ''),
            'location': job_info.get('location', ''),
            'experience': job_info.get('experience', ''),
            'education': job_info.get('education', ''),
            'company': job_info.get('company', ''),
            'salary_range': self.get('salary_range', ''),
            'skills': self.get('skills', ''),
            'greeting': self.render_prompt(self.get('greeting_prompt', ''), {
                'job_title': job_info.get('title', ''),
                'company': job_info.get('company', ''),
                'salary_range': self.get('salary_range', ''),
                'skills': self.get('skills', ''),
            })
        }
        return self.render_prompt(template, variables)


# 全局单例
config = AppConfig()


def get_config():
    """获取全局配置单例"""
    return config
