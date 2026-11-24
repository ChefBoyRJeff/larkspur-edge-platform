{{/*
The name label is fixed: the database NetworkPolicy admits pods labeled
app.kubernetes.io/name=larkspur-track (web pods and the migration job).
*/}}
{{- define "larkspur-track.selectorLabels" -}}
app.kubernetes.io/name: larkspur-track
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "larkspur-track.labels" -}}
{{ include "larkspur-track.selectorLabels" . }}
app.kubernetes.io/version: {{ .Values.image.tag | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end }}

{{- define "larkspur-track.image" -}}
{{ .Values.image.repository }}:{{ required "image.tag is required (an immutable sha-<commit> tag)" .Values.image.tag }}
{{- end }}

{{- define "larkspur-track.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{- define "larkspur-track.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end }}

{{- define "larkspur-track.databaseEnv" -}}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.database.secretName }}
      key: {{ .Values.database.secretKey }}
{{- end }}

{{/* Called with .Values.resources as its context. */}}
{{- define "larkspur-track.resources" -}}
requests:
  cpu: {{ .requests.cpu }}
  memory: {{ .requests.memory }}
limits:
  memory: {{ .limits.memory }}
{{- end }}
