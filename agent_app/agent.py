"""
408刷题Agent - 核心智能体逻辑
实现：自适应出题、判题、LLM解析、错题分析、薄弱点诊断
"""
import random
import json
import requests
from django.conf import settings
from django.db.models import Count, Q, Avg
from .models import Question, Category, PracticeSession, AnswerRecord, WrongBook


# ============================================================
#  1. 自适应出题引擎
# ============================================================
class QuestionSelector:
    """根据用户历史表现，自适应选择题目"""

    @staticmethod
    def select(category_slug=None, difficulty=None, count=10, exclude_ids=None, knowledge_point=None):
        """出题：可指定科目、难度、知识点，排除已做过的题"""
        qs = Question.objects.all()
        if category_slug:
            qs = qs.filter(category__slug=category_slug)
        if difficulty:
            qs = qs.filter(difficulty=difficulty)
        if knowledge_point:
            qs = qs.filter(knowledge_point__icontains=knowledge_point)
        if exclude_ids:
            qs = qs.exclude(id__in=exclude_ids)

        # 如果指定难度题目不够，混入其他难度
        selected = list(qs)
        if len(selected) < count:
            fallback = Question.objects.exclude(id__in=exclude_ids or [])
            if category_slug:
                fallback = fallback.filter(category__slug=category_slug)
            needed = count - len(selected)
            extra = list(fallback.exclude(id__in=[s.id for s in selected]))[:needed]
            selected += extra

        random.shuffle(selected)
        return selected[:count]

    @staticmethod
    def adaptive_select(category_slug=None, count=10):
        """自适应出题：结合错题本和用户水平"""
        # 获取错题本中未掌握的题目
        weak_ids = WrongBook.objects.filter(
            mastered=False,
            question__category__slug=category_slug
        ).values_list('question_id', flat=True) if category_slug else WrongBook.objects.filter(
            mastered=False
        ).values_list('question_id', flat=True)

        weak_questions = list(Question.objects.filter(id__in=weak_ids))
        # 取一半错题重练，一半新题
        weak_count = min(len(weak_questions), count // 2)
        new_count = count - weak_count

        selected_weak = random.sample(weak_questions, weak_count) if weak_questions else []
        # 新题排除错题
        new_questions = QuestionSelector.select(
            category_slug=category_slug,
            count=new_count + weak_count,  # 多取一些备用
            exclude_ids=list(weak_ids)
        )[:new_count]

        result = selected_weak + new_questions
        random.shuffle(result)
        return result


# ============================================================
#  2. 判题引擎
# ============================================================
class Grader:
    """判题：比对用户答案与正确答案"""

    @staticmethod
    def check(question_id, user_answer):
        question = Question.objects.get(id=question_id)
        # 标准化比较：去空格、大写
        correct = question.answer.strip().upper()
        user = user_answer.strip().upper()
        is_correct = (correct == user)
        return {
            'question_id': question_id,
            'user_answer': user_answer,
            'correct_answer': question.answer,
            'is_correct': is_correct,
        }


# ============================================================
#  3. 智能答疑引擎（LLM优先 + 本地智能回退）
# ============================================================
import re

class LLMExplainer:
    """智能答疑引擎：优先调用LLM，无API Key时使用本地智能解析"""

    # ---- LLM调用 ----
    @staticmethod
    def _call_llm(prompt, max_tokens=500):
        """调用LLM API（OpenAI兼容格式）"""
        api_key = settings.LLM_API_KEY
        if not api_key:
            return None
        try:
            resp = requests.post(
                f'{settings.LLM_API_BASE}/chat/completions',
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json',
                },
                json={
                    'model': settings.LLM_MODEL,
                    'messages': [
                        {'role': 'system', 'content': '你是408计算机考研辅导专家，回答简洁准确。'},
                        {'role': 'user', 'content': prompt},
                    ],
                    'max_tokens': max_tokens,
                    'temperature': 0.3,
                },
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json()['choices'][0]['message']['content']
            return None
        except Exception:
            return None

    # ---- 题目解析 ----
    @classmethod
    def explain(cls, question):
        """为题目生成/获取解析（优先LLM，回退内置）"""
        if question.explanation:
            return question.explanation
        prompt = f"""请为以下408考研题目生成详细解析：

科目：{question.category.name}
知识点：{question.knowledge_point}
题目：{question.content}
选项：
A. {question.option_a}
B. {question.option_b}
C. {question.option_c}
D. {question.option_d}
正确答案：{question.answer}

请按以下格式回复：
1. 考点分析
2. 解题思路
3. 选项分析（每个选项为什么对/错）
4. 易错提醒"""
        result = cls._call_llm(prompt)
        return result or cls._build_explanation(question)

    @classmethod
    def _build_explanation(cls, q):
        """本地构建题目解析"""
        expl = q.explanation or ''
        parts = [f'【考点】{q.knowledge_point}']
        parts.append(f'【正确答案】{q.answer}')
        if expl:
            parts.append(f'【解析】{expl}')
        else:
            parts.append(f'【解析】该题考察{q.knowledge_point}相关知识点。')
        return '\n\n'.join(parts)

    # ================================================================
    #  答疑对话（核心）
    # ================================================================
    @classmethod
    def answer_question(cls, chat_history, current_question):
        """回答用户关于题目的提问"""
        # 1. 尝试LLM
        prompt = f"""用户正在做一道408题目：
科目：{current_question.category.name}
题目：{current_question.content}
选项：
A. {current_question.option_a}
B. {current_question.option_b}
C. {current_question.option_c}
D. {current_question.option_d}
正确答案：{current_question.answer}
内置解析：{current_question.explanation or '无'}

对话历史：
{chat_history}

请用简洁准确的中文回答用户的问题，结合题目具体内容分析。"""
        llm_result = cls._call_llm(prompt, max_tokens=400)
        if llm_result:
            return llm_result

        # 2. 本地智能回复
        return cls._local_answer(chat_history, current_question)

    # ================================================================
    #  本地智能答疑
    # ================================================================
    @classmethod
    def _local_answer(cls, chat_history, question):
        """本地智能答疑：意图识别 + 结构化回复"""
        user_msg = cls._extract_last_msg(chat_history)
        intent, target_opt = cls._detect_intent(user_msg)

        handlers = {
            'why_correct':    cls._resp_why_correct,
            'why_wrong':      cls._resp_why_wrong,
            'full_explain':   cls._resp_full_explain,
            'what_concept':   cls._resp_what_concept,
            'what_answer':    cls._resp_what_answer,
            'hint':           cls._resp_hint,
            'option_analysis': cls._resp_option_analysis,
            'compare':        cls._resp_compare,
            'general':        cls._resp_general,
        }
        handler = handlers.get(intent, cls._resp_general)
        return handler(question, target_opt, user_msg)

    @classmethod
    def _extract_last_msg(cls, chat_history):
        """从对话历史中提取最后一条用户消息"""
        lines = chat_history.strip().split('\n')
        for line in reversed(lines):
            if '用户：' in line:
                return line.split('用户：', 1)[-1].strip()
        return ''

    @classmethod
    def _detect_intent(cls, msg):
        """意图识别 + 选项提取"""
        msg_lower = msg.lower().strip()

        # 提取目标选项
        target_opt = cls._extract_option(msg)

        # 按优先级匹配意图
        patterns = [
            ('why_wrong',      ['为什么错', '为啥错', '错在哪', '不对', '错误', '错在',
                                '为什么不选', '怎么不选', '哪里错', '有何错误']),
            ('why_correct',    ['为什么选', '为什么是', '为啥选', '为什么对', '怎么对的',
                                '怎么选', '理由', '原因', '依据']),
            ('option_analysis',['分析选项', '每个选项', '选项分析', '逐一分析', '分别讲']),
            ('full_explain',   ['解释', '解析', '讲一下', '说说', '分析一下', '详解', '讲解',
                                '怎么理解', '什么意思', '不明白', '不懂', '讲讲']),
            ('what_concept',   ['考点', '考什么', '知识点', '概念', '原理', '理论', '定义',
                                '考察什么', '涉及什么']),
            ('hint',           ['提示', 'hint', '不会', '不知道', '帮帮我', '给点思路',
                                '怎么做', '思路', '启发']),
            ('what_answer',    ['答案', '选什么', '正确选项', '哪个对', '选哪个', '是什么',
                                '请问这题']),
            ('compare',        ['区别', '对比', '比较', '异同', '有什么不同']),
        ]

        for intent, keywords in patterns:
            for kw in keywords:
                if kw in msg_lower:
                    return intent, target_opt

        return 'general', target_opt

    @classmethod
    def _extract_option(cls, msg):
        """从消息中提取选项字母 A/B/C/D"""
        m = re.search(r'[选项]?\s*([A-Dabcd])', msg)
        if m:
            return m.group(1).upper()
        # 中文映射
        mapping = {'A': ['a', '一', '甲'], 'B': ['b', '二', '乙'],
                   'C': ['c', '三', '丙'], 'D': ['d', '四', '丁']}
        for opt, aliases in mapping.items():
            for a in aliases:
                if a in msg:
                    return opt
        return None

    # ================================================================
    #  各类回复生成器
    # ================================================================
    @classmethod
    def _opt_text(cls, q, opt):
        """获取选项文本"""
        opts = {'A': q.option_a, 'B': q.option_b, 'C': q.option_c, 'D': q.option_d}
        return opts.get(opt, '')

    @classmethod
    def _all_options(cls, q):
        """获取所有选项的字典"""
        return {
            'A': {'text': q.option_a, 'is_correct': q.answer.strip().upper() == 'A'},
            'B': {'text': q.option_b, 'is_correct': q.answer.strip().upper() == 'B'},
            'C': {'text': q.option_c, 'is_correct': q.answer.strip().upper() == 'C'},
            'D': {'text': q.option_d, 'is_correct': q.answer.strip().upper() == 'D'},
        }

    @classmethod
    def _resp_why_correct(cls, q, target_opt, msg):
        """解释为什么某个选项正确"""
        correct = q.answer.strip().upper()
        ask_opt = target_opt or correct

        lines = [f'📌 关于选项 {ask_opt}：']
        if ask_opt == correct:
            lines.append(f'✅ {ask_opt} 是正确答案。')
        else:
            lines.append(f'❌ {ask_opt} 不是正确答案，正确答案是 {correct}。')

        lines.append(f'')
        lines.append(f'【选项 {ask_opt}】{cls._opt_text(q, ask_opt)}')

        if q.explanation:
            lines.append(f'')
            lines.append(f'【详细解析】{q.explanation}')
        else:
            lines.append(f'')
            lines.append(f'【解析】该题考察「{q.knowledge_point}」，请结合相关知识点理解。')

        # 如果用户问的不是正确答案，也分析正确答案
        if ask_opt != correct:
            lines.append(f'')
            lines.append(f'💡 正确答案 {correct} 的分析：{cls._opt_text(q, correct)}')
            lines.append(f'如果你对 {correct} 为什么对也有疑问，可以继续问我。')

        lines.append(f'')
        lines.append(f'📝 你可以继续问：「{correct}为什么对？」「其他选项错在哪？」「再解释一下」')
        return '\n'.join(lines)

    @classmethod
    def _resp_why_wrong(cls, q, target_opt, msg):
        """解释为什么某个选项错误"""
        correct = q.answer.strip().upper()
        if not target_opt:
            # 用户没指定选项，列出所有错误选项
            target_opt = [o for o in ['A','B','C','D'] if o != correct][0]

        lines = [f'🔍 分析选项 {target_opt} 为什么错：']
        lines.append(f'')
        lines.append(f'【选项 {target_opt}】{cls._opt_text(q, target_opt)}')
        lines.append(f'【正确答案】{correct}：{cls._opt_text(q, correct)}')

        if q.explanation:
            lines.append(f'')
            lines.append(f'【解析】{q.explanation}')
        else:
            lines.append(f'')
            lines.append(f'【解析】该题考察「{q.knowledge_point}」。')
            # 尝试给出一个推断
            lines.append(f'选项 {target_opt} 的说法不符合该知识点的正确表述。建议回顾教材中关于「{q.knowledge_point}」的内容。')

        lines.append(f'')
        lines.append(f'📝 继续问：「其他选项呢？」「{correct}为什么对？」「换个角度解释」')
        return '\n'.join(lines)

    @classmethod
    def _resp_full_explain(cls, q, target_opt, msg):
        """完整解析题目"""
        correct = q.answer.strip().upper()
        opts = cls._all_options(q)

        lines = [f'📚 题目完整解析', f'']
        lines.append(f'【科目】{q.category.name}')
        lines.append(f'【知识点】{q.knowledge_point}')
        lines.append(f'【题目】{q.content}')
        lines.append(f'')

        # 选项分析
        lines.append(f'【选项分析】')
        for opt in ['A', 'B', 'C', 'D']:
            mark = '✅' if opts[opt]['is_correct'] else '❌'
            lines.append(f'  {mark} {opt}. {opts[opt]["text"]}')
        lines.append(f'')

        # 解析
        if q.explanation:
            lines.append(f'【详细解析】{q.explanation}')
        else:
            lines.append(f'【解析】正确答案为 {correct}。该题考察「{q.knowledge_point}」。')
            lines.append(f'建议回顾教材中该知识点的相关概念和典型应用。')

        lines.append(f'')
        lines.append(f'【易错提醒】做这类题时注意区分相似概念，仔细审题。')
        lines.append(f'')
        lines.append(f'📝 继续问：「{correct}为什么对？」「某个选项错在哪？」「相关知识点还有哪些？」')
        return '\n'.join(lines)

    @classmethod
    def _resp_what_concept(cls, q, target_opt, msg):
        """解释考点和相关知识"""
        correct = q.answer.strip().upper()

        lines = [f'🎯 考点分析', f'']
        lines.append(f'【核心考点】{q.knowledge_point}')
        lines.append(f'【所属科目】{q.category.name}')
        lines.append(f'')

        # 408知识点速查
        concept_hints = cls._get_concept_hint(q.knowledge_point)
        if concept_hints:
            lines.append(f'【知识要点】')
            for hint in concept_hints:
                lines.append(f'  • {hint}')
            lines.append(f'')

        lines.append(f'【本题考察点】{q.content}')
        lines.append(f'【正确答案】{correct}：{cls._opt_text(q, correct)}')

        if q.explanation:
            lines.append(f'')
            lines.append(f'【解析】{q.explanation}')

        lines.append(f'')
        lines.append(f'📝 继续问：「详细解释一下」「这个知识点容易混淆的是什么？」')
        return '\n'.join(lines)

    @classmethod
    def _resp_what_answer(cls, q, target_opt, msg):
        """揭示答案（先给解析再给答案）"""
        correct = q.answer.strip().upper()

        lines = [f'💡 这道题的答案是……先别急，我们一起看看：', f'']
        lines.append(f'【题目】{q.content}')
        lines.append(f'')
        lines.append(f'【正确答案】{correct}. {cls._opt_text(q, correct)}')

        if q.explanation:
            lines.append(f'')
            lines.append(f'【解析】{q.explanation}')
        else:
            lines.append(f'')
            lines.append(f'【简析】该题考察「{q.knowledge_point}」。')

        lines.append(f'')
        lines.append(f'⚠️ 建议先理解为什么选 {correct}，而不是只记答案。')
        lines.append(f'📝 问：「为什么选{correct}？」「其他选项错在哪？」')
        return '\n'.join(lines)

    @classmethod
    def _resp_hint(cls, q, target_opt, msg):
        """给提示而不直接给答案"""
        correct = q.answer.strip().upper()

        lines = [f'💭 解题提示（不直接告诉答案哦~）', f'']
        lines.append(f'【考点】{q.knowledge_point}')

        # 基于知识点给提示
        hints_map = {
            '时间复杂度': '关注操作次数与数据规模的关系，是否涉及循环嵌套。',
            '线索二叉树': '回顾线索二叉树的定义：利用空链域存放前驱/后继指针。',
            '排序': '注意区分稳定排序和不稳定排序，以及各种排序的平均/最坏时间复杂度。',
            '二叉树': '回想二叉树的性质：n0 = n2 + 1，以及完全二叉树的结点编号规律。',
            '图': '回忆图的基本定理（握手定理）和相关算法（遍历、最短路径、最小生成树）。',
            '查找': '区分顺序查找、折半查找、分块查找的适用条件和时间复杂度。',
            '栈': '栈是LIFO结构，考虑入栈出栈的顺序约束。',
            '队列': '队列是FIFO结构，注意队头队尾指针的移动规则。',
            '链表': '链表不能随机访问，但插入删除O(1)；注意头结点的作用。',
            '哈夫曼': '哈夫曼树只有度为0和度为2的结点，总结点数=2n0-1。',
            '散列表': '关注散列函数、冲突处理方法（开放定址/链地址）和查找效率。',
            'B树': 'B树和B+树的区别：B+树所有数据在叶子结点，叶子结点形成有序链表。',
            'CPU': '理解CPI、MIPS、CPU执行时间的计算公式。',
            'Cache': '关注Cache映射方式（直接/组相联/全相联）和替换算法。',
            '流水线': '流水线吞吐率和加速比的计算，注意流水段数。',
            '中断': '区分中断和异常，理解中断响应过程和中断向量表。',
            '进程': '进程状态转换图是关键，注意哪些转换是可能的。',
            '死锁': '死锁四个必要条件（互斥/请求保持/不可剥夺/循环等待）和银行家算法。',
            '页面置换': '区分FIFO/LRU/OPT/Clock算法，注意Bélády异常。',
            '磁盘': '磁盘访问时间=寻道时间+旋转延迟+传输时间；关注磁盘调度算法。',
            '文件': '文件物理结构：连续/链接/索引分配；FCB的内容。',
            'IP': 'IP地址分类、子网划分、CIDR；注意网络地址和广播地址的计算。',
            'TCP': 'TCP三次握手四次挥手；流量控制和拥塞控制的区别。',
            '路由': 'RIP(距离向量)/OSPF(链路状态)/BGP(路径向量)的区别。',
            'DNS': 'DNS解析过程：递归查询和迭代查询。',
            'HTTP': 'HTTP状态码含义；HTTP/1.1与HTTP/2的区别。',
        }

        for kw, hint in hints_map.items():
            if kw in q.knowledge_point:
                lines.append(f'')
                lines.append(f'【思路启发】{hint}')
                break
        else:
            lines.append(f'')
            lines.append(f'【思路启发】回顾「{q.knowledge_point}」的核心概念，逐一排除明显错误的选项。')

        lines.append(f'')
        lines.append(f'【题目回顾】{q.content}')
        lines.append(f'  A. {q.option_a}')
        lines.append(f'  B. {q.option_b}')
        lines.append(f'  C. {q.option_c}')
        lines.append(f'  D. {q.option_d}')

        lines.append(f'')
        lines.append(f'💪 试着从上述思路出发，自己推导一下答案！')
        lines.append(f'📝 如果还是想不出来，问：「给答案」「再给点提示」')
        return '\n'.join(lines)

    @classmethod
    def _resp_option_analysis(cls, q, target_opt, msg):
        """逐一分析所有选项"""
        correct = q.answer.strip().upper()
        opts = cls._all_options(q)

        lines = [f'🔬 逐选项分析', f'']
        lines.append(f'【题目】{q.content}')
        lines.append(f'【正确答案】{correct}')
        lines.append(f'')

        for opt in ['A', 'B', 'C', 'D']:
            is_correct = opts[opt]['is_correct']
            mark = '✅ 正确' if is_correct else '❌ 错误'
            lines.append(f'{opt}. {opts[opt]["text"]}')
            lines.append(f'   → {mark}')
            if is_correct and q.explanation:
                lines.append(f'   → 原因：{q.explanation}')
            lines.append(f'')

        if not q.explanation:
            lines.append(f'【解析】该题考察「{q.knowledge_point}」，正确答案 {correct} 符合该知识点的定义/性质。')

        lines.append(f'')
        lines.append(f'📝 继续问：「{correct}为什么对？」「某个选项再解释一下」')
        return '\n'.join(lines)

    @classmethod
    def _resp_compare(cls, q, target_opt, msg):
        """对比分析（针对概念对比类问题）"""
        correct = q.answer.strip().upper()
        lines = [f'⚖️ 概念对比', f'']
        lines.append(f'【本题考点】{q.knowledge_point}')
        lines.append(f'【正确答案】{correct}：{cls._opt_text(q, correct)}')

        if q.explanation:
            lines.append(f'')
            lines.append(f'【解析】{q.explanation}')

        lines.append(f'')
        lines.append(f'💡 对比类题目解题技巧：')
        lines.append(f'  1. 先明确每个概念的定义和适用场景')
        lines.append(f'  2. 找出核心区别点（如时间/空间/是否稳定/适用条件）')
        lines.append(f'  3. 用排除法去掉明显错误的选项')

        lines.append(f'')
        lines.append(f'📝 继续问：「详细解析」「换个方式讲」')
        return '\n'.join(lines)

    @classmethod
    def _resp_general(cls, q, target_opt, msg):
        """通用回复：理解用户意图，给出有用回答"""
        correct = q.answer.strip().upper()
        msg_lower = msg.lower()

        # 打招呼
        if any(w in msg_lower for w in ['你好', 'hello', 'hi', '在吗']):
            return (
                f'你好！我正在分析这道「{q.knowledge_point}」的题目。\n\n'
                f'你可以问我：\n'
                f'  • "解析一下" → 完整题目解析\n'
                f'  • "为什么选{correct}？" → 解释正确答案\n'
                f'  • "考点是什么？" → 知识点分析\n'
                f'  • "给个提示" → 不直接给答案的解题思路\n'
                f'  • "{chr(ord("A") + (ord(correct) - ord("A") + 1) % 4)}为什么错？" → 分析错误选项\n\n'
                f'试试看吧！'
            )

        # 默认：给一个综合回复
        lines = [f'🤔 关于这道题：', f'']
        lines.append(f'【题目】{q.content}')
        lines.append(f'【考点】{q.knowledge_point}  |  【科目】{q.category.name}')
        lines.append(f'')

        if q.explanation:
            lines.append(f'【解析】{q.explanation}')
        else:
            lines.append(f'【正确答案】{correct}. {cls._opt_text(q, correct)}')
            lines.append(f'【解析】该题考察「{q.knowledge_point}」相关知识。')

        lines.append(f'')
        lines.append(f'💬 你可以这样问我：')
        lines.append(f'  "解析一下" — 获得完整解析')
        lines.append(f'  "为什么选{correct}？" — 深入理解正确答案')
        lines.append(f'  "给个提示" — 自己推导的解题思路')
        lines.append(f'  "其他选项错在哪？" — 逐一排除错误选项')
        return '\n'.join(lines)

    # ================================================================
    #  知识点速查库
    # ================================================================
    _CONCEPT_HINTS = {
        '时间复杂度': ['时间复杂度衡量算法执行时间随输入规模增长的趋势', '常见复杂度排序：O(1) < O(logn) < O(n) < O(nlogn) < O(n²) < O(2ⁿ)', '顺序表随机访问O(1)，插入删除O(n)'],
        '链表': ['链表不支持随机访问，只能顺序遍历', '插入删除不需要移动元素，时间复杂度O(1)', '注意区分头指针、头结点、首元结点'],
        '栈和队列': ['栈：后进先出(LIFO)，只在栈顶操作', '队列：先进先出(FIFO)，队头删除、队尾插入', '循环队列：队空front==rear，队满(rear+1)%max==front'],
        '二叉树': ['二叉树性质：n0 = n2 + 1（叶子数 = 度为2结点数 + 1）', '第i层最多2^(i-1)个结点，深度为k最多2^k - 1个结点', '完全二叉树：结点编号从1开始，左孩子2i，右孩子2i+1'],
        '哈夫曼树': ['哈夫曼树只有度为0和度为2的结点（无度为1的结点）', '叶子结点数n0 = n，总结点数 = 2n - 1', '哈夫曼编码是前缀编码，是最优变长编码'],
        '图': ['握手定理：所有顶点度数之和 = 边数的2倍', '图的存储：邻接矩阵O(n²)空间、邻接表O(n+e)空间', 'DFS用栈（递归），BFS用队列'],
        '排序': ['稳定排序：冒泡、插入、归并、基数', '不稳定排序：选择、快速、希尔、堆', '快速排序平均O(nlogn)，最坏O(n²)（初始有序时）'],
        '查找': ['折半查找要求顺序存储且有序，时间复杂度O(logn)', '分块查找：块间有序、块内无序，效率介于顺序和折半之间', '散列表查找效率取决于装填因子α和冲突处理方法'],
        'B树': ['B树所有结点都存放数据，B+树只有叶子存放数据', 'B+树叶子结点形成有序链表，适合范围查询', 'm阶B树：每个结点最多m个孩子，至少⌈m/2⌉个孩子（根除外）'],
        '进程': ['进程三状态：就绪 → 运行 → 阻塞', '进程是资源分配的基本单位，线程是调度的基本单位', 'PCB是进程存在的唯一标志'],
        '死锁': ['死锁四条件：互斥、请求保持、不可剥夺、循环等待', '银行家算法：通过安全序列判断是否可分配资源', '死锁预防破坏必要条件，死锁避免动态检查'],
        '页面置换': ['OPT（最佳）：淘汰未来最远使用的页（理论最优，不可实现）', 'LRU（最近最久未用）：淘汰最长时间未被访问的页', 'FIFO可能出现Bélády异常：增加物理块反而增加缺页', 'Clock（时钟）：扫描访问位，0淘汰1清零，改进型加修改位'],
        'TCP': ['三次握手：SYN→SYN+ACK→ACK', '四次挥手：FIN→ACK，FIN→ACK', '流量控制用滑动窗口，拥塞控制有慢启动/拥塞避免/快重传/快恢复'],
        'IP': ['IP地址分类：A(1-126), B(128-191), C(192-223)', '私有地址：10.x, 172.16-31.x, 192.168.x', 'CIDR用网络前缀代替分类，支持路由聚合'],
    }

    @classmethod
    def _get_concept_hint(cls, knowledge_point):
        """根据知识点标签获取速查提示"""
        hints = []
        for kw, items in cls._CONCEPT_HINTS.items():
            if kw in knowledge_point:
                hints.extend(items[:2])  # 最多给2条
        return hints[:4]  # 总共最多4条


# ============================================================
#  4. 薄弱点诊断引擎
# ============================================================
class WeaknessAnalyzer:
    """分析用户的薄弱知识点"""

    @staticmethod
    def analyze():
        """基于错题本分析薄弱点"""
        # 按知识点统计错误次数
        wrong_stats = (
            WrongBook.objects.filter(mastered=False)
            .values('question__category__name', 'question__knowledge_point')
            .annotate(error_count=Count('id'), total_wrong=Count('wrong_count'))
            .order_by('-error_count')
        )

        # 按科目统计正确率
        category_stats = []
        for cat in Category.objects.all():
            total = AnswerRecord.objects.filter(question__category=cat).count()
            correct = AnswerRecord.objects.filter(question__category=cat, is_correct=True).count()
            rate = round(correct / total * 100, 1) if total > 0 else None
            category_stats.append({
                'category': cat.name,
                'slug': cat.slug,
                'total': total,
                'correct': correct,
                'accuracy': rate,
            })

        return {
            'weak_points': list(wrong_stats),
            'category_stats': category_stats,
            'total_wrong_book': WrongBook.objects.filter(mastered=False).count(),
        }

    @staticmethod
    def recommend_difficulty(category_slug=None):
        """根据最近表现推荐练习难度"""
        recent_records = AnswerRecord.objects.order_by('-answered_at')[:30]
        if category_slug:
            recent_records = recent_records.filter(question__category__slug=category_slug)
        if not recent_records:
            return 1  # 默认基础

        correct = recent_records.filter(is_correct=True).count()
        total = recent_records.count()
        accuracy = correct / total

        if accuracy >= 0.8:
            return 3  # 较难
        elif accuracy >= 0.5:
            return 2  # 中等
        else:
            return 1  # 基础
