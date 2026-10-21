from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

class LogoutGetView(auth_views.LogoutView):
    http_method_names = ['get', 'post', 'options']

    def get(self, request, *args, **kwargs):
        return self.post(request, *args, **kwargs)

urlpatterns = [
    path('login/', auth_views.LoginView.as_view(template_name='screener/login.html'), name='login'),
    path('logout/', LogoutGetView.as_view(next_page='login'), name='logout'),
    path('register/', views.register, name='register'),
    path('verify/<str:token>/', views.verify_email, name='verify_email'),
    path('resend-verification/', views.resend_verification, name='resend_verification'),
    path('check-email/<str:email>/', views.check_email, name='check_email'),
]