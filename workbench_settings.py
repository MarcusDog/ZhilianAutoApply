"""Validated public controls. Blank installation defaults never submit resumes."""
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlparse
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator

CN = timezone(timedelta(hours=8))
CITY_CODES = {'北京':'530','上海':'538','深圳':'765','广州':'763','杭州':'653','南京':'635',
              '苏州':'639','成都':'801','西安':'854','武汉':'736','长沙':'749','重庆':'551',
              '天津':'531','郑州':'719','合肥':'664','厦门':'682','青岛':'703','济南':'702'}

class ApplicationSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    processing_mode: Literal['preview','apply'] = 'preview'
    search_terms: list[str] = Field(default_factory=lambda:['Python','AI应用'])
    cities: list[str] = Field(default_factory=lambda:['深圳'])
    search_url: str = ''
    keyword_mode: Literal['blacklist','whitelist','off'] = 'blacklist'
    filter_keywords: str = ''
    title_keywords: list[str] = Field(default_factory=list)
    detail_keywords: list[str] = Field(default_factory=list)
    salary_min: int = Field(0, ge=0, le=1000000)
    salary_max: int = Field(0, ge=0, le=1000000)
    allow_unknown_salary: bool = True
    favorite_policy: Literal['all','only','exclude'] = 'all'
    detail_required: bool = True
    batch_size: int = Field(30, ge=1, le=200)
    apply_interval_seconds: float = Field(10, ge=1, le=3600)
    batch_interval_seconds: float = Field(60, ge=0, le=86400)
    daily_limit: int = Field(100, ge=0, le=10000)
    total_limit: int = Field(0, ge=0, le=1000000)
    collect_limit: int = Field(100, ge=1, le=2000)
    loop_enabled: bool = True
    auto_start: bool = False
    schedule_enabled: bool = False
    schedule_time: str = '09:00'
    browser_channel: Literal['auto','chrome','msedge','chromium'] = 'auto'

    @field_validator('search_terms','title_keywords','detail_keywords','cities')
    @classmethod
    def clean_list(cls, value):
        cleaned = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if len(cleaned)>100 or any(len(v)>100 for v in cleaned):
            raise ValueError('最多100项，每项最多100字')
        return cleaned

    @field_validator('cities')
    @classmethod
    def known_cities(cls, value):
        if any(city not in CITY_CODES for city in value):
            raise ValueError('请从支持的城市列表中选择')
        return value

    @field_validator('search_url')
    @classmethod
    def valid_search_url(cls, value):
        if value:
            u=urlparse(value)
            if u.scheme!='https' or u.hostname!='www.zhaopin.com' or not u.path.startswith('/sou'):
                raise ValueError('仅支持 https://www.zhaopin.com/sou/ 下的职位搜索链接')
        return value

    @field_validator('schedule_time')
    @classmethod
    def valid_time(cls,value):
        import re
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',value):
            raise ValueError('时间格式应为HH:MM')
        return value

    @model_validator(mode='after')
    def coherent(self):
        if self.salary_max and self.salary_min>self.salary_max:
            raise ValueError('薪资下限不能大于上限')
        if not self.search_url and (not self.search_terms or not self.cities):
            raise ValueError('请填写搜索关键词并选择城市，或提供搜索链接')
        return self

def load_workbench_settings(config):
    raw = config.get('desktop_settings', None)
    if raw is not None:
        return ApplicationSettings.model_validate(raw)
    # Existing local installations may retain their explicitly authorized choices.
    return ApplicationSettings(processing_mode='apply' if config.get('campaign_authorized',False) else 'preview',
                    search_terms=config.get('preview_search_terms') or ['Python','AI应用'],
                    filter_keywords=config.get('filter_keywords',''),
                    apply_interval_seconds=config.get('campaign_apply_interval_seconds',10),
                    batch_interval_seconds=config.get('campaign_batch_interval_seconds',60))

def store_workbench_settings(config, settings):
    config.load_from_db()
    config.update({'desktop_settings':settings.model_dump(), 'filter_keywords':settings.filter_keywords,
                   'campaign_no_filter':True, 'campaign_batch_size':settings.batch_size,
                   'campaign_apply_interval_seconds':settings.apply_interval_seconds,
                   'campaign_batch_interval_seconds':settings.batch_interval_seconds,
                   'preview_search_terms':settings.search_terms})

def is_start_schedule_due(settings,last_date,now=None):
    now=now or datetime.now(CN)
    return (settings.schedule_enabled and last_date!=now.date().isoformat()
            and now.strftime('%H:%M')>=settings.schedule_time)
