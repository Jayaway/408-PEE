"""
408刷题Agent - 视图与API
"""
import json
from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import Category, Question, PracticeSession, AnswerRecord, WrongBook
from .agent import QuestionSelector, Grader, LLMExplainer, WeaknessAnalyzer


# ============================================================
#  页面视图
# ============================================================
def index(request):
    """主页面 - SPA入口"""
    categories = Category.objects.all()
    return render(request, 'agent_app/index.html', {'categories': categories})


# ============================================================
#  API - 类别与统计
# ============================================================
def api_categories(request):
    """获取所有科目"""
    cats = list(Category.objects.values('id', 'name', 'slug'))
    return JsonResponse({'categories': cats})


def api_stats(request):
    """获取学习统计"""
    stats = WeaknessAnalyzer.analyze()
    total_questions = Question.objects.count()
    total_answered = AnswerRecord.objects.count()
    total_wrong = WrongBook.objects.filter(mastered=False).count()
    stats['summary'] = {
        'total_questions': total_questions,
        'total_answered': total_answered,
        'total_wrong': total_wrong,
    }
    return JsonResponse(stats)


# ============================================================
#  API - 出题
# ============================================================
@csrf_exempt
def api_start_practice(request):
    """开始一轮练习 - 出题"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    data = json.loads(request.body)
    category_slug = data.get('category', None)
    difficulty = data.get('difficulty', None)
    count = min(data.get('count', 10), 200)  # 最多200题（支持知识导图加载）
    mode = data.get('mode', 'new')  # new / adaptive
    knowledge_point = data.get('knowledge_point', None)  # 按知识点过滤

    # 创建会话
    category = None
    if category_slug:
        category = Category.objects.get(slug=category_slug)

    session = PracticeSession.objects.create(
        category=category,
        difficulty=difficulty or 1,
        total_count=count,
    )

    # 选题
    if mode == 'adaptive':
        questions = QuestionSelector.adaptive_select(category_slug, count)
    else:
        questions = QuestionSelector.select(category_slug, difficulty, count, knowledge_point=knowledge_point)

    # 构建返回数据（不含答案）
    questions_data = []
    for q in questions:
        questions_data.append({
            'id': q.id,
            'category': q.category.name,
            'qtype': q.qtype,
            'difficulty': q.difficulty,
            'knowledge_point': q.knowledge_point,
            'content': q.content,
            'options': {
                'A': q.option_a,
                'B': q.option_b,
                'C': q.option_c,
                'D': q.option_d,
            },
        })

    return JsonResponse({
        'session_id': session.id,
        'questions': questions_data,
        'total': len(questions_data),
    })


# ============================================================
#  API - 提交答案
# ============================================================
@csrf_exempt
def api_submit_answer(request):
    """提交单题答案"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    data = json.loads(request.body)
    session_id = data.get('session_id')
    question_id = data.get('question_id')
    user_answer = data.get('answer', '')

    session = PracticeSession.objects.get(id=session_id)
    question = Question.objects.get(id=question_id)

    # 判题
    result = Grader.check(question_id, user_answer)

    # 记录答题
    record = AnswerRecord.objects.create(
        session=session,
        question=question,
        user_answer=user_answer,
        is_correct=result['is_correct'],
    )

    # 更新错题本
    if not result['is_correct']:
        wrong_entry, created = WrongBook.objects.get_or_create(
            question=question,
            defaults={'wrong_count': 1},
        )
        if not created:
            wrong_entry.wrong_count += 1
            wrong_entry.save()
    else:
        # 如果之前错过，检查是否连续正确3次则标记为已掌握
        recent_wrong = WrongBook.objects.filter(question=question).first()
        if recent_wrong:
            consecutive_correct = AnswerRecord.objects.filter(
                question=question, is_correct=True
            ).order_by('-answered_at')[:3].count()
            if consecutive_correct >= 3:
                recent_wrong.mastered = True
                recent_wrong.save()

    # 更新会话统计
    session.correct_count = session.records.filter(is_correct=True).count()

    # 检查是否完成所有题目
    if session.records.count() >= session.total_count:
        from django.utils import timezone
        session.completed_at = timezone.now()

    session.save()

    # 获取解析
    explanation = LLMExplainer.explain(question)

    return JsonResponse({
        'is_correct': result['is_correct'],
        'correct_answer': result['correct_answer'],
        'explanation': explanation,
        'session_progress': {
            'answered': session.records.count(),
            'correct': session.correct_count,
            'total': session.total_count,
        },
    })


# ============================================================
#  API - 会话结果
# ============================================================
def api_session_result(request, session_id):
    """获取一个练习会话的完整结果"""
    session = PracticeSession.objects.get(id=session_id)
    records = session.records.select_related('question').all()

    results = []
    for r in records:
        q = r.question
        results.append({
            'question_id': q.id,
            'content': q.content,
            'options': {
                'A': q.option_a,
                'B': q.option_b,
                'C': q.option_c,
                'D': q.option_d,
            },
            'your_answer': r.user_answer,
            'correct_answer': q.answer,
            'is_correct': r.is_correct,
            'explanation': q.explanation,
            'knowledge_point': q.knowledge_point,
        })

    return JsonResponse({
        'session_id': session.id,
        'category': session.category.name if session.category else '综合',
        'total': session.total_count,
        'correct': session.correct_count,
        'accuracy': round(session.correct_count / max(session.total_count, 1) * 100, 1),
        'completed': session.completed_at is not None,
        'records': results,
    })


# ============================================================
#  API - 错题本
# ============================================================
def api_wrong_book(request):
    """获取错题本"""
    entries = WrongBook.objects.filter(mastered=False).select_related(
        'question__category'
    ).order_by('-last_wrong_at')

    data = []
    for e in entries:
        q = e.question
        data.append({
            'id': q.id,
            'category': q.category.name,
            'knowledge_point': q.knowledge_point,
            'content': q.content,
            'options': {
                'A': q.option_a,
                'B': q.option_b,
                'C': q.option_c,
                'D': q.option_d,
            },
            'answer': q.answer,
            'explanation': q.explanation,
            'wrong_count': e.wrong_count,
        })

    return JsonResponse({'wrong_book': data, 'total': len(data)})


@csrf_exempt
def api_mark_mastered(request):
    """标记错题已掌握"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    data = json.loads(request.body)
    question_id = data.get('question_id')
    WrongBook.objects.filter(question_id=question_id).update(mastered=True)
    return JsonResponse({'success': True})


# ============================================================
#  API - AI对话（题目答疑）
# ============================================================
@csrf_exempt
def api_chat(request):
    """用户对当前题目提问"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    data = json.loads(request.body)
    question_id = data.get('question_id')
    message = data.get('message', '')
    chat_history = data.get('history', '')

    question = Question.objects.get(id=question_id)
    chat_context = f'{chat_history}\n用户：{message}'
    reply = LLMExplainer.answer_question(chat_context, question)

    return JsonResponse({'reply': reply})


# ============================================================
#  API - 推荐难度
# ============================================================
def api_recommend(request):
    """获取推荐练习难度"""
    category_slug = request.GET.get('category', None)
    difficulty = WeaknessAnalyzer.recommend_difficulty(category_slug)
    return JsonResponse({'recommended_difficulty': difficulty})
