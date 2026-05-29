from django.urls import path
from . import views

urlpatterns = [
    # 页面
    path('', views.index, name='index'),

    # API - 基础
    path('api/categories/', views.api_categories, name='api_categories'),
    path('api/stats/', views.api_stats, name='api_stats'),
    path('api/recommend/', views.api_recommend, name='api_recommend'),

    # API - 练习
    path('api/practice/start/', views.api_start_practice, name='api_start_practice'),
    path('api/practice/submit/', views.api_submit_answer, name='api_submit_answer'),
    path('api/practice/result/<int:session_id>/', views.api_session_result, name='api_session_result'),

    # API - 错题本
    path('api/wrongbook/', views.api_wrong_book, name='api_wrong_book'),
    path('api/wrongbook/mastered/', views.api_mark_mastered, name='api_mark_mastered'),

    # API - AI对话
    path('api/chat/', views.api_chat, name='api_chat'),
]
