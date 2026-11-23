#version 330 core
// Clean-room ScaleFX pass 1: corner interpolation strength (ARCHITECTURE.md §13.4.3, ADR-069).
// Reads Pass 0 perceptual distances and outputs four symmetric corner strengths in RGBA8:
//   r = TL, g = TR, b = BR, a = BL.

uniform sampler2D u_input_tex;
uniform vec2 u_in_origin;
uniform vec2 u_in_size;
uniform vec2 u_in_tex_size;

in vec2 v_uv;
out vec4 f_color;

const float EDGE_THRESH = 0.40;
const float EQ_THRESH = 0.08;

vec4 fetch_m(ivec2 p, ivec2 origin, int tex_h) {
    ivec2 texel = ivec2(origin.x + p.x, tex_h - 1 - (origin.y + p.y));
    return texelFetch(u_input_tex, texel, 0);
}

float d_right(ivec2 p, ivec2 origin, ivec2 src_size, int tex_h) {
    if (p.x < 0 || p.x >= src_size.x - 1) {
        return 0.0;
    }
    ivec2 q = ivec2(p.x, clamp(p.y, 0, src_size.y - 1));
    return fetch_m(q, origin, tex_h).r;
}

float d_down(ivec2 p, ivec2 origin, ivec2 src_size, int tex_h) {
    if (p.y < 0 || p.y >= src_size.y - 1) {
        return 0.0;
    }
    ivec2 q = ivec2(clamp(p.x, 0, src_size.x - 1), p.y);
    return fetch_m(q, origin, tex_h).g;
}

float d_dr(ivec2 p, ivec2 origin, ivec2 src_size, int tex_h) {
    ivec2 q1 = clamp(p, ivec2(0), src_size - ivec2(1));
    ivec2 q2 = clamp(p + ivec2(1, 1), ivec2(0), src_size - ivec2(1));
    if (q1 == q2) {
        return 0.0;
    }
    if (q1.x == q2.x) {
        return fetch_m(q1, origin, tex_h).g;
    }
    if (q1.y == q2.y) {
        return fetch_m(q1, origin, tex_h).r;
    }
    return fetch_m(q1, origin, tex_h).b;
}

float d_dl(ivec2 p, ivec2 origin, ivec2 src_size, int tex_h) {
    ivec2 q1 = clamp(p, ivec2(0), src_size - ivec2(1));
    ivec2 q2 = clamp(p + ivec2(-1, 1), ivec2(0), src_size - ivec2(1));
    if (q1 == q2) {
        return 0.0;
    }
    if (q1.x == q2.x) {
        return fetch_m(q1, origin, tex_h).g;
    }
    if (q1.y == q2.y) {
        return fetch_m(q2, origin, tex_h).r;
    }
    return fetch_m(q1, origin, tex_h).a;
}

bool eq_d(float d) {
    return d <= EQ_THRESH;
}

// Evaluates one corner in canonical bottom-right orientation (diagonal F--H separating E from I).
float eval_strength(
    float d_ef, float d_eh, float d_fh,
    float d_eb, float d_ed, float d_fi, float d_hi,
    float d_ea, float d_ec, float d_eg
) {
    if (d_ef <= EQ_THRESH || d_eh <= EQ_THRESH) {
        return 0.0;
    }
    if (d_fh >= EDGE_THRESH || d_fh >= 0.5 * min(d_ef, d_eh)) {
        return 0.0;
    }

    // Anti-dither orthogonal-support gate: blocks checkerboards while passing 1px diagonals
    bool has_support = eq_d(d_eb) || eq_d(d_ed) || eq_d(d_fi) || eq_d(d_hi);
    if (!has_support) {
        return 0.0;
    }

    // Preserve sharp 90-degree corners of >=2x2 axis-aligned solid blocks
    bool is_box_corner = eq_d(d_eb) && eq_d(d_ed) && eq_d(d_ea)
                      && !eq_d(d_ec) && !eq_d(d_eg);
    if (is_box_corner) {
        return 0.0;
    }

    float clr = max(0.0, 1.0 - d_fh / EDGE_THRESH) * min(d_ef, d_eh);
    return clamp(clr, 0.0, 1.0);
}

void main() {
    ivec2 origin = ivec2(round(u_in_origin));
    ivec2 src_size = ivec2(round(u_in_size));
    int tex_h = int(round(u_in_tex_size.y));

    vec2 top_uv = vec2(v_uv.x, 1.0 - v_uv.y);
    ivec2 p0 = clamp(ivec2(floor(top_uv * u_in_size)), ivec2(0), src_size - ivec2(1));

    // Orthogonal adjacencies around E = p0
    float d_ef = d_right(p0, origin, src_size, tex_h);
    float d_eh = d_down(p0, origin, src_size, tex_h);
    float d_eb = d_down(p0 + ivec2(0, -1), origin, src_size, tex_h);
    float d_ed = d_right(p0 + ivec2(-1, 0), origin, src_size, tex_h);

    float d_ab = d_right(p0 + ivec2(-1, -1), origin, src_size, tex_h);
    float d_ad = d_down(p0 + ivec2(-1, -1), origin, src_size, tex_h);
    float d_bc = d_right(p0 + ivec2(0, -1), origin, src_size, tex_h);
    float d_cf = d_down(p0 + ivec2(1, -1), origin, src_size, tex_h);
    float d_fi = d_down(p0 + ivec2(1, 0), origin, src_size, tex_h);
    float d_hi = d_right(p0 + ivec2(0, 1), origin, src_size, tex_h);
    float d_dg = d_down(p0 + ivec2(-1, 0), origin, src_size, tex_h);
    float d_gh = d_right(p0 + ivec2(-1, 1), origin, src_size, tex_h);

    // Diagonal adjacencies
    float d_ea = d_dr(p0 + ivec2(-1, -1), origin, src_size, tex_h);
    float d_ec = d_dl(p0 + ivec2(1, -1), origin, src_size, tex_h);
    float d_eg = d_dl(p0, origin, src_size, tex_h);
    float d_ei = d_dr(p0, origin, src_size, tex_h);

    float d_bd = d_dl(p0 + ivec2(0, -1), origin, src_size, tex_h);
    float d_bf = d_dr(p0 + ivec2(0, -1), origin, src_size, tex_h);
    float d_fh = d_dl(p0 + ivec2(1, 0), origin, src_size, tex_h);
    float d_dh = d_dr(p0 + ivec2(-1, 0), origin, src_size, tex_h);

    float s_br = eval_strength(d_ef, d_eh, d_fh, d_eb, d_ed, d_fi, d_hi, d_ea, d_ec, d_eg);
    float s_bl = eval_strength(d_eh, d_ed, d_dh, d_ef, d_eb, d_gh, d_dg, d_ec, d_ei, d_ea);
    float s_tl = eval_strength(d_ed, d_eb, d_bd, d_eh, d_ef, d_ad, d_ab, d_ei, d_eg, d_ec);
    float s_tr = eval_strength(d_eb, d_ef, d_bf, d_ed, d_eh, d_bc, d_cf, d_eg, d_ea, d_ei);

    f_color = vec4(s_tl, s_tr, s_br, s_bl);
}
