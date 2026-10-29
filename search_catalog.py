"""Legacy scraper constants. Actual user filters are in position_filters.py."""
from workbench_settings import CITY_CODES
SEARCH_TERMS=['Python','AI应用']

def rank_collected_position(job):
    return dict(review_status='direct_pending',score=0,reasons=['等待按当前配置处理'],uncertainties=[])
