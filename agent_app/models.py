from django.db import models


class Category(models.Model):
    """408四科"""
    name = models.CharField(max_length=50, unique=True)  # 数据结构/计算机组成原理/操作系统/计算机网络
    slug = models.SlugField(max_length=20, unique=True)  # ds/co/os/cn
    order = models.IntegerField(default=0)  # 排序

    class Meta:
        ordering = ['order']

    def __str__(self):
        return self.name


class Question(models.Model):
    """题库"""
    DIFFICULTY_CHOICES = [
        (1, '基础'),
        (2, '中等'),
        (3, '较难'),
    ]
    TYPE_CHOICES = [
        ('single', '单选题'),
        ('multi', '多选题'),
        ('judge', '判断题'),
        ('fill', '填空题'),
    ]

    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='questions')
    qtype = models.CharField(max_length=10, choices=TYPE_CHOICES, default='single')
    difficulty = models.IntegerField(choices=DIFFICULTY_CHOICES, default=1)
    knowledge_point = models.CharField(max_length=100)  # 知识点标签，如"页面置换算法"
    content = models.TextField()  # 题干预文本
    option_a = models.CharField(max_length=500, blank=True)
    option_b = models.CharField(max_length=500, blank=True)
    option_c = models.CharField(max_length=500, blank=True)
    option_d = models.CharField(max_length=500, blank=True)
    answer = models.CharField(max_length=100)  # 正确答案，如"A"、"AB"、"True"、"<填空文本>"
    explanation = models.TextField(blank=True)  # 解析

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['category', 'difficulty', 'id']

    def __str__(self):
        return f'[{self.category.name}][L{self.difficulty}] {self.content[:40]}'


class PracticeSession(models.Model):
    """一次练习会话"""
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    difficulty = models.IntegerField(default=1)  # 选择的难度
    total_count = models.IntegerField(default=10)  # 本次题目数
    correct_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']


class AnswerRecord(models.Model):
    """单题答题记录"""
    session = models.ForeignKey(PracticeSession, on_delete=models.CASCADE, related_name='records')
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    user_answer = models.CharField(max_length=100)  # 用户答案
    is_correct = models.BooleanField()
    answered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['answered_at']


class WrongBook(models.Model):
    """错题本"""
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='wrong_entries')
    wrong_count = models.IntegerField(default=1)  # 错了几次
    last_wrong_at = models.DateTimeField(auto_now=True)
    mastered = models.BooleanField(default=False)  # 是否已掌握

    class Meta:
        ordering = ['-last_wrong_at']

    def __str__(self):
        return f'{self.question} (错{self.wrong_count}次)'
