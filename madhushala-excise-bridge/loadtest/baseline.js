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
    bridge_errors: ['rate<0.01'],
    bridge_root_latency: ['p(95)<750', 'p(99)<1500'],
  },
};

const baseUrl = (__ENV.BASE_URL || 'http://127.0.0.1:8091').replace(/\/$/, '');

export default function () {
  const response = http.get(`${baseUrl}/`, { tags: { endpoint: 'root' } });
  rootLatency.add(response.timings.duration);
  errorRate.add(response.status !== 200);
  check(response, { 'root 200': (r) => r.status === 200 });
  sleep(0.25);
}
