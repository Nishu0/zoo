{{/* Names */}}
{{- define "zoo.fullname" -}}
{{- if contains "zoo" .Release.Name -}}
{{- .Release.Name | trunc 50 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-zoo" .Release.Name | trunc 50 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "zoo.labels" -}}
app.kubernetes.io/name: zoo
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{/* selector labels for one component: include "zoo.selector" (list . "api") */}}
{{- define "zoo.selector" -}}
app.kubernetes.io/name: zoo
app.kubernetes.io/instance: {{ (index . 0).Release.Name }}
app.kubernetes.io/component: {{ index . 1 }}
{{- end -}}

{{- define "zoo.serviceAccount" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "zoo.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "zoo.secretName" -}}
{{- default (printf "%s-secrets" (include "zoo.fullname" .)) .Values.secrets.existingSecret -}}
{{- end -}}

{{- define "zoo.sandboxNamespace" -}}
{{- default (printf "%s-sandboxes" .Release.Namespace) .Values.sandboxes.namespace -}}
{{- end -}}

{{- define "zoo.tag" -}}
{{- default .Chart.AppVersion .Values.image.tag -}}
{{- end -}}

{{/* include "zoo.image" (list . "api") */}}
{{- define "zoo.image" -}}
{{- $root := index . 0 -}}
{{- printf "%s/zoo-%s:%s" $root.Values.image.registry (index . 1) (include "zoo.tag" $root) -}}
{{- end -}}

{{- define "zoo.desktopImage" -}}
{{- default (include "zoo.image" (list . "sandbox-desktop")) .Values.sandboxes.images.desktop -}}
{{- end -}}

{{- define "zoo.publicUrl" -}}
{{- printf "%s://%s" .Values.ingress.scheme .Values.ingress.host -}}
{{- end -}}

{{- define "zoo.apiUrl" -}}
{{- printf "%s://%s" .Values.ingress.scheme .Values.ingress.apiHost -}}
{{- end -}}

{{- define "zoo.gatewayHost" -}}
{{- printf "%s-gateway.%s.svc.cluster.local" (include "zoo.fullname" .) .Release.Namespace -}}
{{- end -}}

{{/* Environment for every Zoo pod; the component's ZOO_ROLE is set where it is used */}}
{{- define "zoo.env" -}}
- name: ZOO_RUNTIME
  value: kube
- name: ZOO_API_URL
  value: {{ include "zoo.apiUrl" . | quote }}
- name: CORS_ORIGINS
  value: {{ include "zoo.publicUrl" . | quote }}
- name: FORWARDED_ALLOW_IPS
  value: "*"
- name: ZOO_METRICS_PORT
  value: {{ .Values.metrics.port | quote }}
- name: ZOO_SANDBOX_IMAGE
  value: {{ include "zoo.desktopImage" . | quote }}
- name: ZOO_BROWSER_IMAGE
  value: {{ default (include "zoo.image" (list . "sandbox-browser")) .Values.sandboxes.images.browser | quote }}
- name: ZOO_CODE_IMAGE
  value: {{ default (include "zoo.image" (list . "sandbox-code")) .Values.sandboxes.images.code | quote }}
- name: ZOO_EGRESS_IMAGE
  value: {{ include "zoo.image" (list . "egress") | quote }}
- name: ZOO_GATEWAY_PEERS
  value: {{ printf "%s-gateway-peers.%s.svc.cluster.local" (include "zoo.fullname" .) .Release.Namespace | quote }}
- name: ZOO_GATEWAY_SERVICE
  value: {{ printf "%s-gateway.%s.svc.cluster.local" (include "zoo.fullname" .) .Release.Namespace | quote }}
- name: ZOO_GATEWAY_GRPC_PORT
  value: "7444"
- name: ZOO_NODE_HOST
  value: {{ default .Values.ingress.apiHost .Values.gateway.nodes.host | quote }}
- name: ZOO_NODE_PORT
  value: {{ .Values.gateway.nodes.service.port | quote }}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
{{- if eq .Values.database.mode "cnpg" }}
      name: {{ include "zoo.fullname" . }}-db-app
      key: uri
{{- else if eq .Values.database.mode "external" }}
      name: {{ required "database.external.existingSecret is required with database.mode=external" .Values.database.external.existingSecret }}
      key: {{ .Values.database.external.key }}
{{- else }}
{{- fail "database.mode must be cnpg or external" }}
{{- end }}
{{- with .Values.secrets.kms }}
- name: ZOO_KMS
  value: {{ . | quote }}
{{- end }}
- name: ZOO_OBJECT_STORE
  value: {{ required "objectStorage.url (s3://bucket[/prefix]) is required: profiles, screenshots, snapshots and backups live there" .Values.objectStorage.url | quote }}
{{- with .Values.objectStorage.endpoint }}
- name: ZOO_S3_ENDPOINT
  value: {{ . | quote }}
{{- end }}
{{- with .Values.objectStorage.region }}
- name: ZOO_S3_REGION
  value: {{ . | quote }}
{{- end }}
{{- if .Values.sandboxes.enabled }}
- name: ZOO_NAMESPACE
  value: {{ include "zoo.sandboxNamespace" . | quote }}
- name: ZOO_STORAGE_CLASS
  value: {{ .Values.sandboxes.storageClass | quote }}
- name: ZOO_KUBERNETES_HOME_SIZE
  value: {{ .Values.sandboxes.homeSize | quote }}
- name: ZOO_SNAPSHOT_CLASS
  value: {{ .Values.sandboxes.snapshotClass | quote }}
- name: ZOO_IDLE_MEMORY_MB_DESKTOP
  value: {{ .Values.sandboxes.idleMemoryMbDesktop | quote }}
- name: ZOO_IDLE_MEMORY_MB_CODE
  value: {{ .Values.sandboxes.idleMemoryMbCode | quote }}
- name: ZOO_SPARE_VOLUMES
  value: {{ .Values.sandboxes.spareVolumes | quote }}
- name: ZOO_PAUSE_MINUTES
  value: {{ .Values.sandboxes.pauseMinutes | quote }}
- name: ZOO_KUBERNETES_IMAGE_PULL_SECRET
  value: {{ .Values.sandboxes.imagePullSecret | quote }}
- name: ZOO_KUBERNETES_EGRESS_SELECTOR
  value: {{ printf "app.kubernetes.io/component=egress,app.kubernetes.io/instance=%s" .Release.Name | quote }}
{{- end }}
- name: ZOO_MAX_SANDBOX_CPUS
  value: {{ .Values.limits.maxSandboxCpus | quote }}
- name: ZOO_MAX_SANDBOX_MEMORY_MB
  value: {{ .Values.limits.maxSandboxMemoryMb | quote }}
- name: ZOO_MAX_SANDBOX_DISK_GB
  value: {{ .Values.limits.maxSandboxDiskGb | quote }}
{{- range $name, $value := .Values.limits.defaultQuota }}
{{- if $value }}
- name: {{ printf "ZOO_DEFAULT_QUOTA_%s" ($name | snakecase | upper) }}
  value: {{ $value | quote }}
{{- end }}
{{- end }}
- name: ZOO_BACKUP_INTERVAL_HOURS
  value: {{ .Values.backups.intervalHours | quote }}
- name: ZOO_BACKUP_KEEP
  value: {{ .Values.backups.keep | quote }}
- name: ZOO_EMBED_ORIGINS
  value: {{ .Values.viewer.embedOrigins | quote }}
- name: ZOO_MAX_VIEWER_LINK_HOURS
  value: {{ .Values.viewer.maxLinkHours | quote }}
- name: ZOO_EVENT_RETENTION_DAYS
  value: {{ .Values.retention.eventDays | quote }}
- name: ZOO_WEBHOOK_ALLOW_PRIVATE
  value: {{ ternary "1" "" .Values.webhooks.allowPrivate | quote }}
{{- range $name, $value := .Values.env }}
- name: {{ $name }}
  value: {{ $value | quote }}
{{- end }}
{{- end -}}

{{- define "zoo.envFrom" -}}
- secretRef:
    name: {{ include "zoo.secretName" . }}
{{- with .Values.objectStorage.existingSecret }}
- secretRef:
    name: {{ . }}
{{- end }}
{{- with .Values.envFrom }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/* The pod spec of a Zoo process: include "zoo.serverPod" (list . "api" .Values.api) */}}
{{- define "zoo.serverPod" -}}
{{- $root := index . 0 -}}
{{- $role := index . 1 -}}
{{- $values := index . 2 -}}
serviceAccountName: {{ include "zoo.serviceAccount" $root }}
{{- with $root.Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with $root.Values.podSecurityContext }}
securityContext:
  {{- toYaml . | nindent 2 }}
{{- end }}
# long-lived connections (desktop viewers, agent streams) and draining jobs get this long
terminationGracePeriodSeconds: 60
containers:
  - name: zoo
    image: {{ include "zoo.image" (list $root "api") }}
    imagePullPolicy: {{ $root.Values.image.pullPolicy }}
    env:
      - name: ZOO_ROLE
        value: {{ $role }}
      {{- if $values.workers }}
      - name: ZOO_API_WORKERS
        value: {{ $values.workers | quote }}
      {{- end }}
      {{- include "zoo.env" $root | nindent 6 }}
    envFrom:
      {{- include "zoo.envFrom" $root | nindent 6 }}
    ports:
      - name: http
        containerPort: 8000
      - name: metrics
        containerPort: {{ $root.Values.metrics.port }}
    # migrations run as each pod starts (under a lock, scripts/start.sh), after the database is up
    startupProbe:
      httpGet: {path: /healthz, port: http}
      periodSeconds: 5
      failureThreshold: 120
    livenessProbe:
      httpGet: {path: /healthz, port: http}
      periodSeconds: 10
      timeoutSeconds: 5
      failureThreshold: 6
    readinessProbe:
      httpGet: {path: "/readyz?servers=false", port: http}
      periodSeconds: 10
      timeoutSeconds: 8
      failureThreshold: 3
    {{- with $values.resources }}
    resources:
      {{- toYaml . | nindent 6 }}
    {{- end }}
    {{- with $root.Values.securityContext }}
    securityContext:
      {{- toYaml . | nindent 6 }}
    {{- end }}
{{- with $values.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with $values.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with $values.affinity }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}
