<template>
  <ProviderRow :name="displayName" :identifier="backend.name" :status="t(`settings.oauth.${state}`)" :tone="tone" :subtitle="subtitle">
    <p v-if="detail" class="mt-2 text-xs leading-relaxed" :class="error ? 'text-coral' : 'text-warm-400'" :role="error ? 'alert' : undefined">{{ detail }}</p>
    <template #actions>
      <el-button size="small" :loading="busy" :disabled="localHostOnly" @click="load">{{ t("settings.oauth.check") }}</el-button>
      <slot name="actions" />
    </template>
  </ProviderRow>
</template>
<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue"
import { settingsAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"
import ProviderRow from "./ProviderRow.vue"

const props = defineProps({
  backend: { type: Object, required: true },
  node: { type: String, default: "_host" },
})

const { t } = useI18n()
const displayName = computed(() => props.backend.display_name || props.backend.name)
const auth = computed(() => props.backend.auth || {})
const localHostOnly = computed(() => auth.value.local_only === true && !!props.node && props.node !== "_host")
const subtitle = computed(() => {
  if (props.backend.description) return props.backend.description
  return props.backend.package ? t("settings.providers.fromPackage", { name: props.backend.package }) : ""
})
const busy = ref(false)
const status = ref(null)
const error = ref("")
let generation = 0

const state = computed(() => {
  if (localHostOnly.value) return "unsupported"
  if (busy.value) return "checking"
  if (error.value || !status.value) return "failed"
  const view = status.value.state
  if (view === "ready") return "ready"
  if (view === "expired") return status.value.refresh_available ? "pending" : "expired"
  if (view === "login_required") return "missing"
  if (view === "unsupported_platform") return "unsupported"
  if (view === "refresh_busy") return "pending"
  return "attention"
})
const tone = computed(() => (state.value === "ready" ? "success" : ["pending", "expired", "attention"].includes(state.value) ? "warning" : state.value === "failed" ? "danger" : "info"))
const detail = computed(() => {
  if (localHostOnly.value) return t("settings.oauth.localOnly")
  if (error.value) return error.value
  if (["ready", "checking", "unsupported"].includes(state.value)) return ""
  if (state.value === "pending") return t("settings.oauth.autoRefresh")
  const hint = status.value?.hint || auth.value.login_hint
  if (hint) return hint
  return ["missing", "expired"].includes(state.value) ? t("settings.oauth.signInRequired") : ""
})

async function load() {
  const current = ++generation
  status.value = null
  error.value = ""
  if (localHostOnly.value) return
  busy.value = true
  try {
    const result = await settingsAPI.getExternalAuthStatus(props.backend.name, props.node)
    if (current === generation) status.value = result
  } catch (failure) {
    if (current === generation) error.value = t(failure.response?.status === 403 || failure.response?.headers?.["x-auth-required"] === "admin" ? "settings.oauth.adminRequired" : "settings.oauth.checkFailed")
  } finally {
    if (current === generation) busy.value = false
  }
}

watch(() => [props.node, props.backend.name], load, { immediate: true })
onBeforeUnmount(() => {
  generation++
})
</script>
