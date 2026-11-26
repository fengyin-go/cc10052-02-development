import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const receiptRedirect = (paramName = 'importNo') => (to: { params: Record<string, string | string[]> }) => {
  const value = Array.isArray(to.params[paramName]) ? to.params[paramName][0] : to.params[paramName]
  return { path: '/exposure_log', query: { receipt: value } }
}

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    {
      path: '/exposure_log/receipt/:importNo',
      redirect: receiptRedirect(),
    },
    {
      path: '/exposure_log/receipts/:importNo',
      redirect: receiptRedirect(),
    },
    {
      path: '/import_receipts/:importNo',
      redirect: receiptRedirect(),
    },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
