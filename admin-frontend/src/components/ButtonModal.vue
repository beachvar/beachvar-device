<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { NModal, NForm, NFormItem, NInput, NSelect, NSwitch, NButton, NSpace, useMessage } from 'naive-ui'
import type { Button, ButtonCreateDTO, ButtonUpdateDTO } from '@/types'
import { createButton, updateButton, deleteButton } from '@/api/client'

const props = defineProps<{
  show: boolean
  button: Button | null
  nextNumber: number
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
  saved: []
  deleted: []
}>()

const message = useMessage()
const loading = ref(false)

const isEdit = computed(() => props.button !== null)
const modalTitle = computed(() => (isEdit.value ? 'Editar Botao GPIO' : 'Novo Botao GPIO'))

const form = ref<ButtonCreateDTO>({
  button_number: 1,
  gpio_pin: 17,
  label: '',
  is_active: true,
})

const gpioOptions = [
  { label: 'GPIO 17', value: 17 },
  { label: 'GPIO 27', value: 27 },
  { label: 'GPIO 24', value: 24 },
  { label: 'GPIO 5', value: 5 },
  { label: 'GPIO 16', value: 16 },
  { label: 'GPIO 26', value: 26 },
  { label: 'GPIO 6', value: 6 },
  { label: 'GPIO 13', value: 13 },
  { label: 'GPIO 19', value: 19 },
  { label: 'GPIO 22', value: 22 },
]

watch(
  () => props.button,
  (button) => {
    if (button) {
      form.value = {
        button_number: button.button_number,
        gpio_pin: button.gpio_pin,
        label: button.label,
        is_active: button.is_active,
      }
    } else {
      form.value = {
        button_number: props.nextNumber,
        gpio_pin: 17,
        label: '',
        is_active: true,
      }
    }
  },
  { immediate: true }
)

watch(
  () => props.nextNumber,
  (nextNumber) => {
    if (!props.button) {
      form.value.button_number = nextNumber
    }
  }
)

async function handleSave() {
  loading.value = true
  try {
    if (isEdit.value && props.button) {
      const updateData: ButtonUpdateDTO = {
        gpio_pin: form.value.gpio_pin,
        label: form.value.label,
        is_active: form.value.is_active,
      }
      await updateButton(props.button.id, updateData)
      message.success('Botao atualizado')
    } else {
      await createButton(form.value)
      message.success('Botao criado')
    }
    emit('saved')
    emit('update:show', false)
  } catch (error: unknown) {
    const err = error as { response?: { data?: { detail?: string } } }
    message.error(err.response?.data?.detail || 'Erro ao salvar botao')
  } finally {
    loading.value = false
  }
}

async function handleDelete() {
  if (!props.button) return

  if (!confirm('Tem certeza que deseja excluir este botao?')) return

  loading.value = true
  try {
    await deleteButton(props.button.id)
    message.success('Botao excluido')
    emit('deleted')
    emit('update:show', false)
  } catch (error: unknown) {
    const err = error as { response?: { data?: { detail?: string } } }
    message.error(err.response?.data?.detail || 'Erro ao excluir botao')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <NModal
    :show="show"
    @update:show="emit('update:show', $event)"
    preset="card"
    :title="modalTitle"
    :style="{ width: '400px' }"
    :mask-closable="!loading"
    :closable="!loading"
  >
    <NForm :model="form" label-placement="top">
      <NFormItem label="Numero do Botao">
        <NInput
          :value="String(form.button_number)"
          disabled
        />
      </NFormItem>

      <NFormItem label="Pino GPIO">
        <NSelect v-model:value="form.gpio_pin" :options="gpioOptions" />
      </NFormItem>

      <NFormItem label="Label">
        <NInput v-model:value="form.label" placeholder="Ex: Gravar, Live YouTube" />
      </NFormItem>

      <NFormItem label="Ativo">
        <NSwitch v-model:value="form.is_active" />
      </NFormItem>
    </NForm>

    <template #footer>
      <div class="flex justify-between">
        <NButton
          v-if="isEdit"
          type="error"
          ghost
          :loading="loading"
          @click="handleDelete"
        >
          Excluir
        </NButton>
        <div v-else />
        <NSpace>
          <NButton :disabled="loading" @click="emit('update:show', false)">
            Cancelar
          </NButton>
          <NButton type="primary" :loading="loading" @click="handleSave">
            {{ isEdit ? 'Salvar' : 'Criar' }}
          </NButton>
        </NSpace>
      </div>
    </template>
  </NModal>
</template>
