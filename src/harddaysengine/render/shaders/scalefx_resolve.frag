#version 330 core
// Clean-room ScaleFX pass 2: global 2x2 junction resolution and slope classification
// (ARCHITECTURE.md §13.4.3, ADR-069).
// Reads Pass 1 corner strengths (r=TL, g=TR, b=BR, a=BL) and emits discrete codes / 255.0:
//   0 = none, 1 = 45 deg, 2 = 2:1 hori, 3 = 1:2 vert, 4 = 3:1 hori, 5 = 1:3 vert.

uniform sampler2D u_input_tex;
uniform vec2 u_in_origin;
uniform vec2 u_in_size;
uniform vec2 u_in_tex_size;

in vec2 v_uv;
out vec4 f_color;

ivec2 g_origin;
ivec2 g_size;
int g_tex_h;

vec4 fetch_s(ivec2 p) {
    if (p.x < 0 || p.y < 0 || p.x >= g_size.x || p.y >= g_size.y) {
        return vec4(0.0);
    }
    ivec2 texel = ivec2(g_origin.x + p.x, g_tex_h - 1 - (g_origin.y + p.y));
    return texelFetch(u_input_tex, texel, 0);
}

// Resolves the 2x2 junction whose top-left pixel is q:
//   +1 => slash '/' diagonal (q.BR + (q+(1,1)).TL) wins
//   -1 => backslash '\' diagonal ((q+(1,0)).BL + (q+(0,1)).TR) wins
//    0 => tie or inactive (both suppressed)
int junction_winner(ivec2 q) {
    float s_br = fetch_s(q).b;
    float s_bl = fetch_s(q + ivec2(1, 0)).a;
    float s_tr = fetch_s(q + ivec2(0, 1)).g;
    float s_tl = fetch_s(q + ivec2(1, 1)).r;
    float w_slash = s_br + s_tl;
    float w_back = s_bl + s_tr;
    if (w_slash > w_back) {
        return 1;
    }
    if (w_back > w_slash) {
        return -1;
    }
    return 0;
}

bool has_tl(ivec2 p) {
    return fetch_s(p).r > 0.0 && junction_winner(p + ivec2(-1, -1)) > 0;
}

bool has_tr(ivec2 p) {
    return fetch_s(p).g > 0.0 && junction_winner(p + ivec2(0, -1)) < 0;
}

bool has_br(ivec2 p) {
    return fetch_s(p).b > 0.0 && junction_winner(p) > 0;
}

bool has_bl(ivec2 p) {
    return fetch_s(p).a > 0.0 && junction_winner(p + ivec2(-1, 0)) < 0;
}

int classify_br(ivec2 p) {
    if (!has_br(p)) {
        return 0;
    }
    bool diag_c = has_br(p + ivec2(1, -1));
    bool diag_g = has_br(p + ivec2(-1, 1));
    bool is_hori = !diag_c && !diag_g && has_tl(p + ivec2(1, 0)) && !has_br(p + ivec2(-1, 0));
    bool is_vert = !diag_c && !diag_g && has_tl(p + ivec2(0, 1)) && !has_br(p + ivec2(0, -1));
    if (is_hori && !is_vert) {
        bool near_21 = has_br(p + ivec2(2, -1)) || has_br(p + ivec2(-2, 1));
        bool far_31 = has_br(p + ivec2(3, -1)) || has_br(p + ivec2(-3, 1));
        if (!has_bl(p) && !near_21 && far_31) {
            return 4;
        }
        return 2;
    }
    if (is_vert && !is_hori) {
        bool near_12 = has_br(p + ivec2(-1, 2)) || has_br(p + ivec2(1, -2));
        bool far_13 = has_br(p + ivec2(-1, 3)) || has_br(p + ivec2(1, -3));
        if (!has_tr(p) && !near_12 && far_13) {
            return 5;
        }
        return 3;
    }
    return 1;
}

int classify_tl(ivec2 p) {
    if (!has_tl(p)) {
        return 0;
    }
    bool diag_g = has_tl(p + ivec2(-1, 1));
    bool diag_c = has_tl(p + ivec2(1, -1));
    bool is_hori = !diag_g && !diag_c && has_br(p + ivec2(-1, 0)) && !has_tl(p + ivec2(1, 0));
    bool is_vert = !diag_g && !diag_c && has_br(p + ivec2(0, -1)) && !has_tl(p + ivec2(0, 1));
    if (is_hori && !is_vert) {
        bool near_21 = has_tl(p + ivec2(-2, 1)) || has_tl(p + ivec2(2, -1));
        bool far_31 = has_tl(p + ivec2(-3, 1)) || has_tl(p + ivec2(3, -1));
        if (!has_tr(p) && !near_21 && far_31) {
            return 4;
        }
        return 2;
    }
    if (is_vert && !is_hori) {
        bool near_12 = has_tl(p + ivec2(1, -2)) || has_tl(p + ivec2(-1, 2));
        bool far_13 = has_tl(p + ivec2(1, -3)) || has_tl(p + ivec2(-1, 3));
        if (!has_bl(p) && !near_12 && far_13) {
            return 5;
        }
        return 3;
    }
    return 1;
}

int classify_bl(ivec2 p) {
    if (!has_bl(p)) {
        return 0;
    }
    bool diag_a = has_bl(p + ivec2(-1, -1));
    bool diag_i = has_bl(p + ivec2(1, 1));
    bool is_hori = !diag_a && !diag_i && has_tr(p + ivec2(-1, 0)) && !has_bl(p + ivec2(1, 0));
    bool is_vert = !diag_a && !diag_i && has_tr(p + ivec2(0, 1)) && !has_bl(p + ivec2(0, -1));
    if (is_hori && !is_vert) {
        bool near_21 = has_bl(p + ivec2(-2, -1)) || has_bl(p + ivec2(2, 1));
        bool far_31 = has_bl(p + ivec2(-3, -1)) || has_bl(p + ivec2(3, 1));
        if (!has_br(p) && !near_21 && far_31) {
            return 4;
        }
        return 2;
    }
    if (is_vert && !is_hori) {
        bool near_12 = has_bl(p + ivec2(1, 2)) || has_bl(p + ivec2(-1, -2));
        bool far_13 = has_bl(p + ivec2(1, 3)) || has_bl(p + ivec2(-1, -3));
        if (!has_tl(p) && !near_12 && far_13) {
            return 5;
        }
        return 3;
    }
    return 1;
}

int classify_tr(ivec2 p) {
    if (!has_tr(p)) {
        return 0;
    }
    bool diag_i = has_tr(p + ivec2(1, 1));
    bool diag_a = has_tr(p + ivec2(-1, -1));
    bool is_hori = !diag_i && !diag_a && has_bl(p + ivec2(1, 0)) && !has_tr(p + ivec2(-1, 0));
    bool is_vert = !diag_i && !diag_a && has_bl(p + ivec2(0, -1)) && !has_tr(p + ivec2(0, 1));
    if (is_hori && !is_vert) {
        bool near_21 = has_tr(p + ivec2(2, 1)) || has_tr(p + ivec2(-2, -1));
        bool far_31 = has_tr(p + ivec2(3, 1)) || has_tr(p + ivec2(-3, -1));
        if (!has_tl(p) && !near_21 && far_31) {
            return 4;
        }
        return 2;
    }
    if (is_vert && !is_hori) {
        bool near_12 = has_tr(p + ivec2(-1, -2)) || has_tr(p + ivec2(1, 2));
        bool far_13 = has_tr(p + ivec2(-1, -3)) || has_tr(p + ivec2(1, 3));
        if (!has_br(p) && !near_12 && far_13) {
            return 5;
        }
        return 3;
    }
    return 1;
}

void main() {
    g_origin = ivec2(round(u_in_origin));
    g_size = ivec2(round(u_in_size));
    g_tex_h = int(round(u_in_tex_size.y));

    vec2 top_uv = vec2(v_uv.x, 1.0 - v_uv.y);
    ivec2 p0 = clamp(ivec2(floor(top_uv * u_in_size)), ivec2(0), g_size - ivec2(1));

    f_color = vec4(
        float(classify_tl(p0)),
        float(classify_tr(p0)),
        float(classify_br(p0)),
        float(classify_bl(p0))
    ) / 255.0;
}
