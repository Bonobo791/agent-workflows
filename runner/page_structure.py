"""Check explicitly configured Markdown page structure, without guessing rules."""
import json


def policy_bytes(rules,format_name):
    if not isinstance(rules,dict) or not isinstance(format_name,str) or format_name not in rules: raise ValueError('page rules unavailable')
    policy=rules[format_name]
    if not isinstance(policy,dict) or set(policy)!={'headings'} or not isinstance(policy['headings'],list) or not 1<=len(policy['headings'])<=50: raise ValueError('page rules invalid')
    for item in policy['headings']:
        if not isinstance(item,dict) or set(item)!={'level','text'} or type(item['level']) is not int or not 1<=item['level']<=6 or not isinstance(item['text'],str) or not item['text'].strip() or len(item['text'])>200: raise ValueError('page rule invalid')
    if len({(item['level'],item['text']) for item in policy['headings']})!=len(policy['headings']): raise ValueError('duplicate page rule')
    return json.dumps({'format':format_name,'headings':policy['headings']},sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()


def check_page(content,policy):
    if not isinstance(content,str) or len(content.encode('utf-8'))>1048576 or not isinstance(policy,dict) or set(policy)!={'format','headings'}: raise ValueError('page inputs invalid')
    policy_bytes({policy['format']:{'headings':policy['headings']}},policy['format'])
    from markdown_it import MarkdownIt
    tokens=MarkdownIt('commonmark').parse(content); headings=[]
    for index,token in enumerate(tokens):
        if token.type!='heading_open': continue
        inline=tokens[index+1]
        if inline.type!='inline' or any(child.type in ('html_inline','image') for child in inline.children or []): raise ValueError('unsupported heading markup')
        text=''.join(child.content for child in inline.children or [] if child.type in ('text','code_inline')).strip()
        headings.append((int(token.tag[1:]),text))
    position=0
    for item in policy['headings']:
        expected=(item['level'],item['text'])
        try: position=headings.index(expected,position)+1
        except ValueError: return False
    return True
