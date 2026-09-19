import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const errorRate = new Rate('bridge_errors');
const rootLatency = new Trend('bridge_root_latency', true);

export const options = {
  scenarios: {
    baseline: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: [
        { duration: '15s', target: 10 },
        { duration: '30s', target: 25 },
        { duration: '15s', target: 0 },
      ],
      gracefulRampDown: '5s',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<750', 'p(99)<1500'],
    bridge_errors: ['rate<0.01'],
    bridge_root_latency: ['p(95)<1000'],
  },
};

const baseUrl = (__ENV.BASE_URL || 'https://excise.connect.snapkey.in').replace(/\/$/, '');

export default function () {
  const live = http.get(`${baseUrl}/health/live`, { tags: { endpoint: 'health_live' } });
  errorRate.add(live.status !== 200);
  check(live, { 'liveness 200': (r) => r.status === 200 });

  const ready = http.get(`${baseUrl}/health/ready`, { tags: { endpoint: 'health_ready' } });
  errorRate.add(ready.status !== 200);
  check(ready, { 'readiness 200': (r) => r.status === 200 });

  const root = http.get(`${baseUrl}/`, { tags: { endpoint: 'root' } });
  rootLatency.add(root.timings.duration);
  errorRate.add(root.status !== 200);
  check(root, { 'root 200': (r) => r.status === 200 });

  sleep(0.25);
}
