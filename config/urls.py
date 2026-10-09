"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from screener import views # Импорт твоего views.py

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.index, name='index'),
    path('api/data/', views.api_data, name='api_data'),
    path('api/candles/<str:symbol>/', views.api_candles, name='api_candles'),
    path('api/candles-history/<str:symbol>/', views.api_candles_history, name='api_candles_history'),
    path('api/natr/', views.api_natr, name='api_natr'),
    path('api/scalp/<str:symbol>/', views.api_scalp, name='api_scalp'),
    path('api/sound/<str:filename>/', views.api_sound, name='api_sound'),
    path('api/scalp-active/', views.api_scalp_active),
    path('api/mexc-depth/', views.api_mexc_depth, name='api_mexc_depth'),
    path('api/gate-depth/', views.api_gate_depth, name='api_gate_depth'),
    path('api/logo/', views.api_logo),
    path('api/impulse-alerts/', views.api_impulse_alerts, name='api_impulse_alerts'),
    path('api/trades-count/', views.api_trades_count, name='api_trades_count'),
    path('api/exchanges/<str:symbol>/', views.api_exchanges, name='api_exchanges'),
    path('api/binance-check/<str:symbol>/', views.api_binance_check_symbol, name='api_binance_check_symbol'),
    path('api/exchanges-cache-debug/', views.api_exchanges_cache_debug, name='api_exchanges_cache_debug'),
    path('api/exchange-check-debug/<str:symbol>/', views.api_exchange_check_debug, name='api_exchange_check_debug'),
    path('api/exchange-raw-debug/', views.api_exchange_raw_debug, name='api_exchange_raw_debug'),
    path('api/clear-exchange-cache/', views.api_clear_exchange_cache, name='api_clear_exchange_cache'),
    path('api/exchange-alpha-map/', views.api_exchange_alpha_map, name='api_exchange_alpha_map'),
    path('api/alpha-token-list-debug/', views.api_alpha_token_list_debug, name='api_alpha_token_list_debug'),



]