import Redis from 'ioredis';
const redis = new Redis( process.env.REDIS_URL );
const queueName = process.env.QUEUE_NAME || 'python_tasks';

async function publishString(s){ await redis.lpush(queueName, JSON.stringify({ task : s }));}

export { publishString };