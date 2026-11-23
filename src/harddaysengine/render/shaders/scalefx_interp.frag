#version 330 core
// Clean-room ScaleFX pass 3: 3x3 subpixel synthesis (ARCHITECTURE.md §13.4.3, ADR-069).
// Reads Pass 2 discrete corner codes from u_input_tex (unit 0) and exact cropped original RGB
// pixels from u_orig_tex (unit 1).

uniform sampler2D u_input_tex;
uniform vec2 u_in_origin;
uniform vec2 u_in_size;
uniform vec2 u_in_tex_size;

uniform sampler2D u_orig_tex;
uniform vec2 u_orig_origin;
uniform vec2 u_orig_size;
uniform vec2 u_orig_tex_size;

in vec2 v_uv;
out vec4 f_color;

float dist_rgb_sq(vec3 a, vec3 b) {
    float r_mean = 0.5 * (a.r + b.r);
    vec3 d = a - b;
    return (2.0 + r_mean) * d.r * d.r + 4.0 * d.g * d.g + (3.0 - r_mean) * d.b * d.b;
}

vec3 closer_rgb(vec3 e, vec3 a, vec3 b) {
    return (dist_rgb_sq(e, a) <= dist_rgb_sq(e, b)) ? a : b;
}

vec3 fetch_orig(ivec2 base, ivec2 offset, ivec2 origin, ivec2 src_size, int tex_h) {
    ivec2 q = clamp(base + offset, ivec2(0), src_size - ivec2(1));
    ivec2 texel = ivec2(origin.x + q.x, tex_h - 1 - (origin.y + q.y));
    return texelFetch(u_orig_tex, texel, 0).rgb;
}

vec3 pick_two(bool a_on, vec3 a_col, bool b_on, vec3 b_col, vec3 fallback) {
    if (a_on && b_on) {
        return 0.5 * (a_col + b_col);
    }
    if (a_on) {
        return a_col;
    }
    if (b_on) {
        return b_col;
    }
    return fallback;
}

void main() {
    ivec2 orig_origin = ivec2(round(u_orig_origin));
    ivec2 orig_size = ivec2(round(u_orig_size));
    int orig_tex_h = int(round(u_orig_tex_size.y));

    ivec2 in_origin = ivec2(round(u_in_origin));
    ivec2 in_size = ivec2(round(u_in_size));
    int in_tex_h = int(round(u_in_tex_size.y));

    vec2 top_uv = vec2(v_uv.x, 1.0 - v_uv.y);
    vec2 src_pos = top_uv * u_orig_size;
    ivec2 p0 = clamp(ivec2(floor(src_pos)), ivec2(0), orig_size - ivec2(1));
    ivec2 sub = clamp(ivec2(floor((src_pos - vec2(p0)) * 3.0)), ivec2(0), ivec2(2));

    vec3 e_rgb = fetch_orig(p0, ivec2(0, 0), orig_origin, orig_size, orig_tex_h);
    if (sub == ivec2(1, 1)) {
        f_color = vec4(e_rgb, 1.0);
        return;
    }

    ivec2 code_p = clamp(p0, ivec2(0), in_size - ivec2(1));
    ivec2 code_texel = ivec2(in_origin.x + code_p.x, in_tex_h - 1 - (in_origin.y + code_p.y));
    ivec4 codes = ivec4(round(texelFetch(u_input_tex, code_texel, 0) * 255.0));
    int c_tl = codes.r;
    int c_tr = codes.g;
    int c_br = codes.b;
    int c_bl = codes.a;

    if (c_tl == 0 && c_tr == 0 && c_br == 0 && c_bl == 0) {
        f_color = vec4(e_rgb, 1.0);
        return;
    }

    vec3 b_rgb = fetch_orig(p0, ivec2(0, -1), orig_origin, orig_size, orig_tex_h);
    vec3 d_rgb = fetch_orig(p0, ivec2(-1, 0), orig_origin, orig_size, orig_tex_h);
    vec3 f_rgb = fetch_orig(p0, ivec2(1, 0), orig_origin, orig_size, orig_tex_h);
    vec3 h_rgb = fetch_orig(p0, ivec2(0, 1), orig_origin, orig_size, orig_tex_h);

    vec3 col_tl = closer_rgb(e_rgb, d_rgb, b_rgb);
    vec3 col_tr = closer_rgb(e_rgb, b_rgb, f_rgb);
    vec3 col_br = closer_rgb(e_rgb, f_rgb, h_rgb);
    vec3 col_bl = closer_rgb(e_rgb, h_rgb, d_rgb);

    vec3 out_rgb = e_rgb;
    if (sub == ivec2(0, 0)) {
        out_rgb = (c_tl > 0) ? col_tl : pick_two(c_tr == 4, col_tr, c_bl == 5, col_bl, e_rgb);
    } else if (sub == ivec2(2, 0)) {
        out_rgb = (c_tr > 0) ? col_tr : pick_two(c_tl == 4, col_tl, c_br == 5, col_br, e_rgb);
    } else if (sub == ivec2(2, 2)) {
        out_rgb = (c_br > 0) ? col_br : pick_two(c_bl == 4, col_bl, c_tr == 5, col_tr, e_rgb);
    } else if (sub == ivec2(0, 2)) {
        out_rgb = (c_bl > 0) ? col_bl : pick_two(c_br == 4, col_br, c_tl == 5, col_tl, e_rgb);
    } else if (sub == ivec2(1, 0)) {
        out_rgb = pick_two(c_tl == 2 || c_tl == 4, col_tl, c_tr == 2 || c_tr == 4, col_tr, e_rgb);
    } else if (sub == ivec2(1, 2)) {
        out_rgb = pick_two(c_bl == 2 || c_bl == 4, col_bl, c_br == 2 || c_br == 4, col_br, e_rgb);
    } else if (sub == ivec2(0, 1)) {
        out_rgb = pick_two(c_tl == 3 || c_tl == 5, col_tl, c_bl == 3 || c_bl == 5, col_bl, e_rgb);
    } else if (sub == ivec2(2, 1)) {
        out_rgb = pick_two(c_tr == 3 || c_tr == 5, col_tr, c_br == 3 || c_br == 5, col_br, e_rgb);
    }

    f_color = vec4(out_rgb, 1.0);
}
