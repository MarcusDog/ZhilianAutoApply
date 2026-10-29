"""Configurable filters, independent of account identity and graduation year."""
import re
import hashlib
import json
from keyword_rules import parse_keyword_list, find_keyword_match

def parse_salary_range(text):
    text=(text or '').replace(',','').lower()
    if any(word in text for word in ('面议','保密')): return None
    match=re.search(r'(\d+(?:\.\d+)?)\s*([k千万]?)\s*[-~–至]\s*(\d+(?:\.\d+)?)\s*([k千万]?)',text)
    units={'k':1000,'千':1000,'万':10000,'':1}
    if match:
        unit1=match[2] or match[4];unit2=match[4] or match[2]
        low=float(match[1])*units[unit1];high=float(match[3])*units[unit2]
    else:
        match=re.search(r'(\d+(?:\.\d+)?)\s*([k千万])',text)
        if not match:return None
        low=high=float(match[1])*units[match[2]]
    if re.search(r'/年|年薪',text):low/=12;high/=12
    elif re.search(r'/天|/日|日薪|/时|时薪',text):return None
    return low,high

def evaluate_position(job,settings,final=False):
    """Return (eligible, reason); final checks require observable detail/favorite data."""
    words=parse_keyword_list(settings.filter_keywords)
    hit=find_keyword_match(words,job)
    if settings.keyword_mode=='blacklist' and hit:return False,f'黑名单：{hit}'
    if settings.keyword_mode=='whitelist' and words and not hit:
        if final:return False,'未命中白名单'
    title=str(job.get('title') or '')
    if settings.title_keywords and not any(k.casefold() in title.casefold() for k in settings.title_keywords):
        return False,'职位名称不含指定关键词'
    description='\n'.join(str(job.get(k) or '') for k in ('description','requirements'))
    if final:
        if (settings.detail_required or settings.detail_keywords or (words and settings.keyword_mode!='off')) and not description.strip():
            return False,'职位详情未读取完整，暂不提交'
        if settings.detail_keywords and not any(k.casefold() in description.casefold() for k in settings.detail_keywords):
            return False,'职位详情不含指定关键词'
        if settings.favorite_policy!='all':
            favorite=job.get('is_favorite')
            if favorite is None:return False,'收藏状态无法确认，暂不提交'
            if settings.favorite_policy=='only' and not favorite:return False,'仅处理已收藏职位'
            if settings.favorite_policy=='exclude' and favorite:return False,'排除已收藏职位'
    salary=parse_salary_range(job.get('salary',''))
    if salary is None:
        if not settings.allow_unknown_salary:return False,'薪资未知或面议'
    elif (settings.salary_min and salary[1]<settings.salary_min) or (settings.salary_max and salary[0]>settings.salary_max):
        return False,'薪资区间不相交'
    return True,'通过当前过滤规则'

def filter_fingerprint(settings):
    values=settings.model_dump(include={'keyword_mode','filter_keywords','title_keywords','detail_keywords',
        'salary_min','salary_max','allow_unknown_salary','favorite_policy','detail_required'})
    return hashlib.sha256(json.dumps(values,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def record_filter_rejection(job,reason,signature=None):
    if signature:job['filter_signature']=signature
    job.update(review_status='excluded',score=0,reasons=[reason],uncertainties=[])
    if not str(job.get('application_result','')).startswith(('投递成功','投递结果未确认','平台今日上限')):
        job['application_result']='未投递：'+reason
