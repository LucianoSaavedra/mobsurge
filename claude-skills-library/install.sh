#!/usr/bin/env bash
#
# Claude Skills Library - instalador interactivo
# -----------------------------------------------
# Elige escenarios (estilo libreria) e instala las skills de cada uno en
# ~/.claude/skills. Clona cada repo, detecta carpetas con SKILL.md y las enlaza.
#
# Uso:
#   ./install.sh              -> menu interactivo (elige escenarios)
#   ./install.sh base video   -> instala directamente esos escenarios
#   ./install.sh --list       -> lista escenarios disponibles
#   ./install.sh --all        -> instala todos
#
# Requiere: git, bash. En Windows usa WSL o Git Bash.
# NOTA: revisa el codigo de cada repo antes de confiar en el. Instala solo lo que uses.

set -euo pipefail

SKILLS_DIR="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
SOURCES_DIR="$SKILLS_DIR/_sources"

# ---- Catalogo: escenario -> repos (name|url) -----------------------------
# Para agregar skills, edita estas listas (y skills.json para la doc).
declare -A SCENARIO_TITLES=(
  [base]="Colecciones base (empieza por aqui)"
  [ingenieria]="Ingenieria / rendimiento de codigo"
  [ia_productividad]="IA / productividad"
  [estudio]="Estudio / calificaciones"
  [video_viral]="Video para viralizar (Reels/Shorts/TikTok)"
  [marketing]="Marketing para empresas"
  [redes_empresas]="Redes sociales y gestion de empresas"
  [estudio_mercado]="Estudio de mercado"
)

declare -A SCENARIO_REPOS=(
  [base]="anthropics-skills|https://github.com/anthropics/skills
composio-awesome|https://github.com/ComposioHQ/awesome-claude-skills
behisecc-awesome|https://github.com/BehiSecc/awesome-claude-skills"
  [ingenieria]="superpowers|https://github.com/obra/superpowers
everything-claude-code|https://github.com/affaan-m/everything-claude-code"
  [ia_productividad]="anthropics-skills|https://github.com/anthropics/skills"
  [estudio]="anthropics-skills|https://github.com/anthropics/skills"
  [video_viral]="remotion-skills|https://github.com/remotion-dev/skills
remotion-video-skill|https://github.com/wshuyi/remotion-video-skill"
  [marketing]="marketingskills|https://github.com/mysticaltech/marketingskills"
  [redes_empresas]="marketingskills|https://github.com/mysticaltech/marketingskills"
  [estudio_mercado]=""
)

ORDER=(base ingenieria ia_productividad estudio video_viral marketing redes_empresas estudio_mercado)

c_green=$'\033[32m'; c_yellow=$'\033[33m'; c_cyan=$'\033[36m'; c_bold=$'\033[1m'; c_off=$'\033[0m'

info()  { printf '%s\n' "$*"; }
ok()    { printf '%s%s%s\n' "$c_green" "$*" "$c_off"; }
warn()  { printf '%s%s%s\n' "$c_yellow" "$*" "$c_off"; }

list_scenarios() {
  info "${c_bold}Escenarios disponibles:${c_off}"
  local i=1
  for key in "${ORDER[@]}"; do
    printf "  %s%2d)%s %-18s %s\n" "$c_cyan" "$i" "$c_off" "$key" "${SCENARIO_TITLES[$key]}"
    i=$((i+1))
  done
}

# Clona (o actualiza) un repo e instala las skills que contenga.
install_repo() {
  local name="$1" url="$2"
  local dest="$SOURCES_DIR/$name"
  if [ -d "$dest/.git" ]; then
    info "  actualizando $name ..."
    git -C "$dest" pull --ff-only --quiet || warn "  no se pudo actualizar $name (sigo con lo que hay)"
  else
    info "  clonando $name ..."
    if ! git clone --depth 1 --quiet "$url" "$dest"; then
      warn "  FALLO al clonar $name ($url). Lo salto."
      return 0
    fi
  fi

  # Detecta cada carpeta con SKILL.md y la enlaza en SKILLS_DIR.
  local found=0
  while IFS= read -r skillmd; do
    [ -z "$skillmd" ] && continue
    local skilldir; skilldir="$(dirname "$skillmd")"
    local base; base="$(basename "$skilldir")"
    local link="$SKILLS_DIR/$base"
    if [ -e "$link" ] && [ "$(readlink "$link" 2>/dev/null || true)" != "$skilldir" ]; then
      link="$SKILLS_DIR/${name}__${base}"   # evita colisiones de nombre
    fi
    ln -sfn "$skilldir" "$link"
    found=$((found+1))
  done < <(find "$dest" -maxdepth 4 -name SKILL.md -type f 2>/dev/null)

  if [ "$found" -gt 0 ]; then
    ok "  instaladas $found skill(s) de $name"
  else
    warn "  $name no contiene SKILL.md en los primeros niveles; revisa su README manualmente."
  fi
}

install_scenario() {
  local key="$1"
  local repos="${SCENARIO_REPOS[$key]:-}"
  info "${c_bold}>> ${SCENARIO_TITLES[$key]:-$key}${c_off}"
  if [ -z "$repos" ]; then
    warn "  (sin repos instalables: usa la seccion manual del README, p.ej. deep-research)"
    return 0
  fi
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    local rname="${line%%|*}" rurl="${line##*|}"
    install_repo "$rname" "$rurl"
  done <<< "$repos"
}

main() {
  mkdir -p "$SKILLS_DIR" "$SOURCES_DIR"

  if [ "${1:-}" = "--list" ]; then list_scenarios; exit 0; fi

  local selected=()
  if [ "${1:-}" = "--all" ]; then
    selected=("${ORDER[@]}")
  elif [ "$#" -gt 0 ]; then
    selected=("$@")
  else
    list_scenarios
    echo
    read -r -p "Elige numeros separados por espacio (ej: 1 4 5), 'all' para todos: " answer
    if [ "$answer" = "all" ]; then
      selected=("${ORDER[@]}")
    else
      for n in $answer; do
        if [[ "$n" =~ ^[0-9]+$ ]] && [ "$n" -ge 1 ] && [ "$n" -le "${#ORDER[@]}" ]; then
          selected+=("${ORDER[$((n-1))]}")
        else
          warn "Ignoro entrada invalida: $n"
        fi
      done
    fi
  fi

  if [ "${#selected[@]}" -eq 0 ]; then warn "Nada seleccionado. Saliendo."; exit 0; fi

  echo
  info "${c_bold}Instalando en:${c_off} $SKILLS_DIR"
  echo
  for key in "${selected[@]}"; do
    if [ -z "${SCENARIO_TITLES[$key]:-}" ]; then warn "Escenario desconocido: $key (salto)"; continue; fi
    install_scenario "$key"
    echo
  done

  ok "Listo. Reinicia Claude Code para que cargue las skills nuevas."
  info "Para quitar una skill: borra su enlace en $SKILLS_DIR (y su fuente en $SOURCES_DIR)."
}

main "$@"
