"""Legacy scraper constants. Actual user filters are in job_filters.py."""
from settings import CITY_CODES
SEARCH_TERMS=['Python','AI应用']

def assess(job):
    return dict(review_status='direct_pending',score=0,reasons=['等待按当前配置处理'],uncertainties=[])
