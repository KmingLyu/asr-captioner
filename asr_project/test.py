import redis

# 创建 Redis 连接对象
r = redis.Redis(host='asr-redis', port=6379, db=0)

# 进行一个简单的测试
r.set('test', 'value')
value = r.get('test')
print(value)  # 输出应为 b'value'
