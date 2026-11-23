export const auditViewTemplate = `
<section class="panel">
  <h2>审计争用</h2>
  <p class="hint">写动作落审计；后到争用只记 contention，不写业务。持锁列表如实反映 cage_locks：残留锁必须被看见。</p>
  <div class="lock-banner" v-if="locks.length">
    当前持锁 {{ locks.length }} 行。若笼已解笼却仍有占行，说明收尾侧释锁偏航，须按「释锁偏航」处置，禁止只调整下表排序。
  </div>
  <div class="grid-2">
    <div>
      <h3>审计</h3>
      <table>
        <thead><tr><th>时间</th><th>人</th><th>动作</th><th>目标</th><th>载荷</th></tr></thead>
        <tbody>
          <tr v-for="a in audits" :key="a.id">
            <td>{{ a.created_at }}</td>
            <td>{{ a.actor }}</td>
            <td>
              <span class="badge" :class="a.action==='release_lock_drift' ? 'fail' : (a.action==='release_lock_ok' ? 'pass' : 'frozen')">{{ a.action }}</span>
            </td>
            <td>{{ a.target }}</td>
            <td><code style="font-size:11px">{{ a.payload }}</code></td>
          </tr>
        </tbody>
      </table>
    </div>
    <div>
      <h3>持锁（cage_locks）</h3>
      <table>
        <thead><tr><th>键</th><th>持有者</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="l in locks" :key="l.lock_key">
            <td>{{ l.lock_key }}</td>
            <td>{{ l.holder }}</td>
            <td>{{ l.acquired_at }}</td>
          </tr>
          <tr v-if="!locks.length">
            <td colspan="3" class="hint">无持锁</td>
          </tr>
        </tbody>
      </table>
      <h3 style="margin-top:14px">争用</h3>
      <table>
        <thead><tr><th>时间</th><th>键</th><th>人</th><th>动作</th><th>详情</th></tr></thead>
        <tbody>
          <tr v-for="c in contention" :key="c.id">
            <td>{{ c.created_at }}</td>
            <td>{{ c.lock_key }}</td>
            <td>{{ c.actor }}</td>
            <td>{{ c.action }}</td>
            <td>{{ c.detail }}</td>
          </tr>
          <tr v-if="!contention.length">
            <td colspan="5" class="hint">无争用</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</section>
`;
