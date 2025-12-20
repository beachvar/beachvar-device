<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { NModal, NForm, NFormItem, NInput, NSelect, NButton, NSpace, useMessage } from 'naive-ui'
import type { Camera, CameraCreateDTO, CameraUpdateDTO } from '@/types'
import { createCamera, updateCamera, deleteCamera } from '@/api/client'

const props = defineProps<{
  show: boolean
  camera: Camera | null
  courts: { label: string; value: string }[]
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
  saved: []
  deleted: []
}>()

const message = useMessage()
const loading = ref(false)

const isEdit = computed(() => props.camera !== null)
const modalTitle = computed(() => (isEdit.value ? 'Editar Camera' : 'Nova Camera'))

const form = ref<CameraCreateDTO>({
  name: '',
  rtsp_url: '',
  court_id: '',
  position: 'other',
})

const positionOptions = [
  { label: 'Esquerda', value: 'left' },
  { label: 'Direita', value: 'right' },
  { label: 'Centro', value: 'center' },
  { label: 'Aerea', value: 'overhead' },
  { label: 'Outra', value: 'other' },
]

watch(
  () => props.camera,
  (camera) => {
    if (camera) {
      form.value = {
        name: camera.name,
        rtsp_url: camera.rtsp_url,
        court_id: camera.court_id,
        position: camera.position,
      }
    } else {
      form.value = {
        name: '',
        rtsp_url: '',
        court_id: props.courts[0]?.value || '',
        position: 'other',
      }
    }
  },
  { immediate: true }
)

async function handleSave() {
  if (!form.value.name || !form.value.rtsp_url) {
    message.error('Preencha todos os campos obrigatorios')
    return
  }

  loading.value = true
  try {
    if (isEdit.value && props.camera) {
      const updateData: CameraUpdateDTO = {
        name: form.value.name,
        rtsp_url: form.value.rtsp_url,
        position: form.value.position,
      }
      await updateCamera(props.camera.id, updateData)
      message.success('Camera atualizada')
    } else {
      await createCamera(form.value)
      message.success('Camera criada')
    }
    emit('saved')
    emit('update:show', false)
  } catch (error: unknown) {
    const err = error as { response?: { data?: { detail?: string } } }
    message.error(err.response?.data?.detail || 'Erro ao salvar camera')
  } finally {
    loading.value = false
  }
}

async function handleDelete() {
  if (!props.camera) return

  if (!confirm('Tem certeza que deseja excluir esta camera?')) return

  loading.value = true
  try {
    await deleteCamera(props.camera.id)
    message.success('Camera excluida')
    emit('deleted')
    emit('update:show', false)
  } catch (error: unknown) {
    const err = error as { response?: { data?: { detail?: string } } }
    message.error(err.response?.data?.detail || 'Erro ao excluir camera')
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
    :style="{ width: '500px' }"
    :mask-closable="!loading"
    :closable="!loading"
  >
    <NForm :model="form" label-placement="top">
      <NFormItem label="Nome" required>
        <NInput v-model:value="form.name" placeholder="Camera Quadra 1" />
      </NFormItem>

      <NFormItem label="URL RTSP" required>
        <NInput
          v-model:value="form.rtsp_url"
          placeholder="rtsp://user:pass@192.168.1.100:554/stream"
        />
      </NFormItem>

      <NFormItem label="Quadra" required>
        <NSelect
          v-model:value="form.court_id"
          :options="courts"
          placeholder="Selecione a quadra"
          :disabled="isEdit"
        />
      </NFormItem>

      <NFormItem label="Posicao">
        <NSelect v-model:value="form.position" :options="positionOptions" />
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
