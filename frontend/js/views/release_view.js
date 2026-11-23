export const releaseViewTemplate = `
<section class="panel">
  <h2>解笼拨付</h2>
  <p class="hint">仅核验通过的笼可拨付。默认把可用额全额拨给受益人，也可部分解笼。账户余额与冻结同步扣减。出金与释锁同一事务边界：释锁偏航则拨付零改、拒绝亮绿。</p>
  <div class="grid-2">
    <div>
      <div class="form-row">
        <div>
          <label>通过态笼</label>
          <select v-model.number="releaseForm.cage_id">
            <option :value="0">请选择</option>
            <option v-for="c in passCages" :key="c.id" :value="c.id">
              {{ c.cage_code }} · 可用 {{ fmt(c.available) }}
            </option>
          </select>
        </div>
        <div>
          <label>金额（空=全额）</label>
          <input type="number" v-model.number="releaseForm.amount" min="0" step="1000" placeholder="全额"/>
        </div>
        <div>
          <label>收款方（空=受益人）</label>
          <input v-model="releaseForm.payee"/>
        </div>
      </div>
      <div class="actions">
        <button class="ok" @click="doRelease">解笼拨付</button>
      </div>
      <div class="release-note">
        收尾口径：拨付提交前必须删除该笼的 cage_locks 占行；删不掉则整笔回滚并记「释锁偏航」，页面不会显示成功。
      </div>
    </div>
    <div>
      <table>
        <thead>
          <tr><th>凭证</th><th>笼</th><th>金额</th><th>收款方</th><th>笼代</th><th>时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in releases" :key="r.id">
            <td>{{ r.release_ref }}</td>
            <td>{{ r.cage_code }}</td>
            <td>{{ fmt(r.amount) }}</td>
            <td>{{ r.payee }}</td>
            <td>{{ r.cage_generation }}</td>
            <td>{{ r.created_at }}</td>
          </tr>
        </tbody>
      </table>
      <h3 style="margin-top:14px">该笼持锁状态</h3>
      <table>
        <thead><tr><th>笼键</th><th>持有者</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="l in cageLocks" :key="l.lock_key">
            <td>{{ l.lock_key }}</td>
            <td>{{ l.holder }}</td>
            <td><span class="badge fail">残留占笼</span></td>
          </tr>
          <tr v-if="!cageLocks.length">
            <td colspan="3" class="hint">无持锁，收尾已闭环</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</section>
`;
