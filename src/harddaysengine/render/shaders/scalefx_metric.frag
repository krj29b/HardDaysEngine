#version 330 core
// Clean-room ScaleFX pass 0: perceptual Redmean edge-distance metric (ARCHITECTURE.md §13.4.3, ADR-069).
// Stores the four non-redundant neighbour distances per cropped source pixel in RGBA8:
//   r = d(E, right), g = d(E, down), b = d(E, down-right), a = d(E, down-left).

uniform sampler2D u_input_tex;
uniform vec2 u_in_origin;
uniform vec2 u_in_size;
uniform vec2 u_in_tex_size;

in vec2 v_uv;
out vec4 f_color;

vec3 fetch_rgb(ivec2 base, ivec2 offset, ivec2 origin, ivec2 src_size, int tex_h) {
    ivec2 q = clamp(base + offset, ivec2(0), src_size - ivec2(1));
    ivec2 texel = ivec2(origin.x + q.x, tex_h - 1 - (origin.y + q.y));
    return texelFetch(u_input_tex, texel, 0).rgb;
}

float dist_rgb(vec3 a, vec3 b) {
    float r_mean = 0.5 * (a.r + b.r);
    vec3 d = a - b;
    float sq = (
        (2.0 + r_mean) * d.r * d.r
        + 4.0 * d.g * d.g
        + (3.0 - r_mean) * d.b * d.b
    ) / 9.0;
    return sqrt(max(sq, 0.0));
}

void main() {
    ivec2 origin = ivec2(round(u_in_origin));
    ivec2 src_size = ivec2(round(u_in_size));
    int tex_h = int(round(u_in_tex_size.y));

    vec2 top_uv = vec2(v_uv.x, 1.0 - v_uv.y);
    ivec2 p0 = clamp(ivec2(floor(top_uv * u_in_size)), ivec2(0), src_size - ivec2(1));

    vec3 e = fetch_rgb(p0, ivec2(0, 0), origin, src_size, tex_h);
    vec3 f = fetch_rgb(p0, ivec2(1, 0), origin, src_size, tex_h);
    vec3 h = fetch_rgb(p0, ivec2(0, 1), origin, src_size, tex_h);
    vec3 i = fetch_rgb(p0, ivec2(1, 1), origin, src_size, tex_h);
    vec3 g = fetch_rgb(p0, ivec2(-1, 1), origin, src_size, tex_h);

    f_color = vec4(
        dist_rgb(e, f),
        dist_rgb(e, h),
        dist_rgb(e, i),
        dist_rgb(e, g)
    );
}
